═══════════════════════════════════════
  PROMPT: Debug arinanoX XFCE — Desktop Not Rendering
═══════════════════════════════════════

## Context

arinanoX runs XFCE4 desktop inside a Debian 13 proot container on Android/Termux.
Display: Termux:X11 app (termux-x11 nighty). Audio: PulseAudio TCP bridge.

Launch flow:
  1. bash ~/start.sh   → PulseAudio + termux-x11 :0 + API bridge
  2. bash ~/start.sh  → proot-distro login + dbus-launch startxfce4

## Symptoms

- XFCE4-session + xfwm4 processes appear (confirmed via ps)
- "X server already running on display :0" message shows (X11 is up)
- But nothing renders on screen — Termux:X11 app shows black/blank
- This is a fresh Debian 13 image (ghcr.io/arinadi/arinanox:latest)

## Key scripts (refer to arinanoX repo)

launchers/start.sh    — starts pulseaudio, loads TCP module, termux-x11 :0
launchers/start.sh   — proot login, binds X11 socket, dbus-launch startxfce4
launchers/stop.sh     — kills X11 and DELETES .X11-unix/ directory
launchers/stop.sh   — kills XFCE, cleans ICEauthority, sessions cache

## Dockerfile (image layer)

FROM debian:13
Installed: xfce4-session, xfwm4, xfce4-panel, xfce4-settings, xfdesktop4, thunar,
           xfce4-terminal, xfce4-whiskermenu-plugin, xfce4-power-manager,
           xfce4-pulseaudio-plugin, xfce4-genmon-plugin
Not installed: xfce4-goodies, lightdm, gdm3, gnome-*

## What to research

1. TERMUX:X11 CONFIG
   - What does `termux-x11-preference` output?
   - Is there a resolution/fullscreen setting that could cause blank screen?
   - Does Termux:X11 need `-listen tcp` or specific flags?
   - What's the correct way to start termux-x11 for proot usage?
   - Check: `termux-x11 :0 -ac` vs `termux-x11 :0 -ac -listen tcp`

2. XFCE COMPOSITING
   - Does xfwm4 compositor work in proot without GPU?
   - Should compositing be disabled? (xfwm4 --compositor=off)
   - Error seen: "Another compositing manager is running on screen 0"
   - Check: does termux-x11 provide its own compositor?

3. DBUS SESSION
   - Is `dbus-launch --exit-with-session` correct for proot?
   - Should we use `dbus-run-session` instead?
   - Does dbus need --config-file for proot?

4. XFCE FIRST-RUN
   - Does XFCE need xfce4-panel.xml / xfwm4.xml pre-configured?
   - We REMOVED all XFCE config files (image/configs/xfce4-*.xml deleted)
   - Does a first-run without any config cause black screen?

5. PROOT BIND MOUNTS
   - `--shared-tmp` vs explicit `--bind` for X11 socket — conflict?
   - Does `/tmp/.X11-unix` inside proot see the socket?
   - Check: `proot-distro login arinanox -- ls -la /tmp/.X11-unix/`

6. ALTERNATIVE WMs
   - If XFCE fails, does a bare window manager work?
   - Test: `proot-distro login arinanox -- DISPLAY=:0 xfwm4 --replace &`
   - Test: `proot-distro login arinanox -- DISPLAY=:0 xterm`

7. TERMUX:X11 VERSION
   - What version of termux-x11-nightly is installed?
   - Are there known issues with specific versions + proot + XFCE?
   - Check: `dpkg -l termux-x11-nightly`

8. ANDROID SIDE
   - Does Termux:X11 app need "Display over other apps" permission?
   - Is the app in foreground or background?
   - Try: `am start -n com.termux.x11/.MainActivity` then check if it renders

═══════════════════════════════════════
Output needed: specific fix to make XFCE desktop render
