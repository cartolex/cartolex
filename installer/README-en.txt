cartolex - installing on your computer
======================================

cartolex maps a research field from the texts of the people who work in it.
This folder installs it for you, without administrator rights. The installer
downloads about 1 GB (cartolex, the libraries it needs and the language models
for English, French and Portuguese), so use a good connection. It takes a few
minutes.

Texts in Spanish, German or Italian need their own language model, about
45 MB each. At the end, the installer asks which of them to add, and names
each model's licence before downloading it (the Spanish one is under the GNU
GPL, the Italian one is for non-commercial use only). You can add them later
with the command the installer shows.

Everything goes into one folder in your home folder, named "cartolex":
the program, its Python, the downloads and a log of the installation
(install.log). Your projects are kept elsewhere, where you choose to create
them.

To update cartolex, download the newer kit and run its installer: it updates
the installation in place. Your projects are not touched.


macOS
-----

1. Double-click the zip file to unpack it, then open the folder.
2. Right-click (or Control-click) "Install cartolex.command" and choose Open.
   macOS says it cannot check the developer: choose Open again. (A plain
   double-click the first time only shows a warning; right-click > Open is
   the way through. On recent macOS, if there is no Open button: System
   Settings > Privacy & Security, then "Open Anyway" next to the message
   about the installer.)
3. A Terminal window shows the progress. When it says "Done", press Enter.
4. A file "cartolex.command" is now on your desktop. Double-click it to open
   cartolex in your browser. Keep its Terminal window open while you work;
   close it to stop cartolex.


Windows
-------

1. Right-click the zip file, choose "Extract All...", then open the folder.
   (Running the installer from inside the zip without extracting it does
   not work.)
2. Double-click "Install cartolex.bat". Windows may show "Windows protected
   your PC": click "More info", then "Run anyway".
3. A window shows the progress. When it says "Done", press a key to close it.
4. A "cartolex" shortcut is now on your desktop and in the Start menu. It
   opens cartolex in your browser. Keep its black window open while you work;
   close it to stop cartolex.

If the installer says there is no Python on this computer: install Python
from https://www.python.org/downloads/ (tick "Add python.exe to PATH" on the
first screen), then run "Install cartolex.bat" again.


Linux
-----

1. Unpack the zip file and open a terminal in the folder.
2. Make the installer runnable, then run it:
       chmod +x install-cartolex.sh
       ./install-cartolex.sh
3. cartolex is now in your applications menu (and on your desktop when your
   desktop shows files). It opens cartolex in your browser. You can also run
   ~/cartolex/env/bin/cartolex in a terminal.

On Debian or Ubuntu, if the installer asks for it: sudo apt install python3-venv


When the network blocks the installer
-------------------------------------

The installer tries three ways in turn: with uv (a Python installer it
downloads from astral.sh), then with uv trusting the certificates of your
computer (which fixes most networks that inspect secure connections), then
with the Python already installed on your computer. The log says which way
worked, and why the others did not.

If all three fail:
- Behind a proxy, set it before running the installer. In a terminal
  (macOS, Linux): export HTTPS_PROXY=http://proxy.example.org:8080
  then run the installer from that terminal. On Windows, in a command
  prompt: set HTTPS_PROXY=http://proxy.example.org:8080
  then run "Install cartolex.bat" from that prompt. Ask your IT service for
  the address of the proxy.
- Install Python 3.10 or later from https://www.python.org/downloads/ and run
  the installer again: that way needs neither uv nor astral.sh.
- Ask your IT service to allow these addresses: pypi.org,
  files.pythonhosted.org (the Python packages), github.com and
  objects.githubusercontent.com (the language models), astral.sh (uv).
- Or try from another network (at home, for example): once installed,
  cartolex works on any network.

When you ask for help, send the file install.log from the "cartolex" folder
in your home folder.


Removing cartolex
-----------------

Delete the "cartolex" folder in your home folder, and the cartolex shortcut
(on the desktop, in the Start menu or the applications menu). Your projects
are elsewhere and stay.
