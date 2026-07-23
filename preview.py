"""
This script starts a live-reloading Jekyll preview server for the documentation repository. 
It will open a browser window to the local preview webpage and rebuild the site when files change.
If Ruby, Bundler, or the Jekyll theme is missing, it will attempt to install them automatically.
"""

import os
import signal
import shutil
import subprocess
import sys
import time
import webbrowser
from pathlib import Path


# Locate the repository and set the fixed local preview address
ROOT = Path(__file__).resolve().parent
ON_WINDOWS = os.name == "nt"
HOST = "127.0.0.1"
PORT = "4000"
URL = f"http://{HOST}:{PORT}"
SERVE_PROCESS = None


# Message shown when Ruby cannot be installed automatically
RUBY_INSTALL_HINT = (
    "Unable to install Ruby automatically. Please install it and re-run this script.\n"
    "Windows: https://rubyinstaller.org/\n"
    "macOS/Linux: https://www.ruby-lang.org/en/documentation/installation/"
)


# Run a command from the repository root
def run(command, check=True):
    resolved_command = command[:]
    executable = shutil.which(command[0])
    if executable:
        resolved_command[0] = executable
    return subprocess.run(resolved_command, cwd=ROOT, check=check)


# Run a Ruby-installed command without using Windows batch wrappers
def ruby(command):
    return ["ruby", "-S"] + command


# Start Jekyll without using Windows batch wrappers
def run_server(command):
    resolved_command = command[:]
    executable = shutil.which(command[0])
    if executable:
        resolved_command[0] = executable
    popen_options = {"cwd": ROOT}
    if ON_WINDOWS:
        popen_options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_options["start_new_session"] = True
    return subprocess.Popen(resolved_command, **popen_options)


# Wait in short intervals so Ctrl+C is handled by Python
def wait_for_server(process):
    while process.poll() is None:
        time.sleep(0.2)
    return process.returncode


# Stop the preview server and any child processes it started
def stop_server(process):
    if not process or process.poll() is not None:
        return

    if ON_WINDOWS:
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    else:
        os.killpg(process.pid, signal.SIGTERM)

    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


# Make a newly installed tool available to this script
def add_to_path(path):
    path = str(path)
    current_paths = os.environ.get("PATH", "").split(os.pathsep)
    if path not in current_paths:
        os.environ["PATH"] = path + os.pathsep + os.environ.get("PATH", "")


# Use sudo for Linux package managers when it is available and needed
def with_sudo(command):
    if ON_WINDOWS:
        return command
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        return command
    if shutil.which("sudo"):
        return ["sudo"] + command
    return command


# Some installers update PATH for future terminals, but not this one
def refresh_ruby_path():
    if ON_WINDOWS:
        search_roots = [Path("C:/"), Path(os.environ.get("LOCALAPPDATA", ""))]
        ruby_bins = []
        for root in search_roots:
            if root.exists():
                ruby_bins.extend(root.glob("Ruby*/bin"))
                ruby_bins.extend(root.glob("Programs/Ruby*/bin"))

        for ruby_bin in sorted(ruby_bins, reverse=True):
            if (ruby_bin / "ruby.exe").exists():
                add_to_path(ruby_bin)
                return

    if sys.platform == "darwin" and shutil.which("brew"):
        result = subprocess.run(
            ["brew", "--prefix", "ruby"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        ruby_bin = Path(result.stdout.strip()) / "bin"
        if result.returncode == 0 and ruby_bin.exists():
            add_to_path(ruby_bin)


# Make sure the script is being run from the documentation repository
if not (ROOT / "Gemfile").exists():
    print("Gemfile not found. Run this script from the documentation repository.")
    raise SystemExit(1)

try:
    # Check for Ruby before trying to install anything
    refresh_ruby_path()

    if not shutil.which("ruby"):
        print("Ruby was not found. Installing Ruby...")

        # Build a list of platform-appropriate Ruby installation attempts
        install_plans = []
        if ON_WINDOWS:
            # RubyInstaller with DevKit is the most reliable Jekyll path on Windows
            if shutil.which("winget"):
                for ruby_version in ["3.4", "3.3", "3.2"]:
                    install_plans.append(
                        [
                            [
                                "winget",
                                "install",
                                "--id",
                                f"RubyInstallerTeam.RubyWithDevKit.{ruby_version}",
                                "--exact",
                                "--source",
                                "winget",
                                "--accept-package-agreements",
                                "--accept-source-agreements",
                            ]
                        ]
                    )
            if shutil.which("choco"):
                install_plans.append([["choco", "install", "ruby", "-y"]])
        elif sys.platform == "darwin":
            if shutil.which("brew"):
                install_plans.append([["brew", "install", "ruby"]])
        else:
            # Jekyll gems often need native extensions, so include build tools
            if shutil.which("apt-get"):
                install_plans.append(
                    [
                        with_sudo(["apt-get", "update"]),
                        with_sudo(
                            [
                                "apt-get",
                                "install",
                                "-y",
                                "ruby-full",
                                "build-essential",
                                "zlib1g-dev",
                            ]
                        ),
                    ]
                )
            elif shutil.which("dnf"):
                install_plans.append(
                    [with_sudo(["dnf", "install", "-y", "ruby", "ruby-devel", "gcc", "make"])]
                )
            elif shutil.which("yum"):
                install_plans.append(
                    [with_sudo(["yum", "install", "-y", "ruby", "ruby-devel", "gcc", "make"])]
                )
            elif shutil.which("pacman"):
                install_plans.append(
                    [with_sudo(["pacman", "-S", "--needed", "--noconfirm", "ruby", "base-devel"])]
                )
            elif shutil.which("zypper"):
                install_plans.append(
                    [with_sudo(["zypper", "install", "-y", "ruby", "ruby-devel", "gcc", "make"])]
                )
            elif shutil.which("apk"):
                install_plans.append([with_sudo(["apk", "add", "ruby", "ruby-dev", "build-base"])])

        if not install_plans:
            print("No supported package manager was found for automatic Ruby installation.")
            print(RUBY_INSTALL_HINT)
            raise SystemExit(1)

        # Try each install plan until one succeeds and Ruby appears on PATH
        for plan in install_plans:
            plan_failed = False
            for command in plan:
                result = run(command, check=False)
                if result.returncode != 0:
                    plan_failed = True
                    break

            refresh_ruby_path()
            if not plan_failed and shutil.which("ruby"):
                break

        # If Ruby still is not visible, the user may need a fresh terminal session
        if not shutil.which("ruby"):
            print("Ruby installation did not complete, or Ruby is not available in this terminal yet.")
            print("Try closing and reopening your terminal, then run this script again.")
            print(RUBY_INSTALL_HINT)
            raise SystemExit(1)

    # Install Bundler when Ruby is present but Bundler is missing
    if run(ruby(["bundle", "--version"]), check=False).returncode != 0:
        print("Bundler was not found. Installing it...")
        run(ruby(["gem", "install", "bundler"]))

    # Install the Jekyll theme and other gems from the Gemfile when needed
    if run(ruby(["bundle", "check"]), check=False).returncode != 0:
        print("Installing site dependencies...")
        run(ruby(["bundle", "install"]))

    # Open the browser shortly after Jekyll starts so the first page can load
    print(f"Opening {URL}")
    webbrowser.open(URL)

    # Start the live-reloading Jekyll preview server
    print(f"Serving site at {URL}")
    print("Press Ctrl+C to stop.")
    SERVE_PROCESS = run_server(
        ruby(
            [
                "bundle",
                "exec",
                "jekyll",
                "serve",
                "--livereload",
                "--host",
                HOST,
                "--port",
                PORT,
            ]
        )
    )
    exit_code = wait_for_server(SERVE_PROCESS)
    if exit_code:
        raise SystemExit(exit_code)

# Keep Ctrl+C friendly when stopping the preview server
except KeyboardInterrupt:
    print("\nStopping preview server...")
    stop_server(SERVE_PROCESS)
    print("Stopped preview server.")
    raise SystemExit(0)

# Report command failures without a Python traceback
except subprocess.CalledProcessError as error:
    print(f"Command failed with exit code {error.returncode}: {subprocess.list2cmdline(error.cmd)}")
    raise SystemExit(error.returncode)
