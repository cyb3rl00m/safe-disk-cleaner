# Security policy

Safe Disk Cleaner runs a small helper as root (through `pkexec`) to remove system files you
selected. Bugs in that helper are taken seriously.

## Reporting a vulnerability

Please **do not open a public issue** for security problems. Instead use GitHub's private reporting:
go to the **Security** tab of this repository → **Report a vulnerability**.

Include what you did, what happened, and the version (`safe-disk-cleaner --version`).
You will get an answer as soon as possible, and credit in the release notes if you like.

## What counts as a security issue

- The root helper deleting, changing or running anything outside its whitelist
  (see "How it keeps your system safe" in the README)
- A way to make the app remove the running kernel, the newest kernel, or packages you didn't select
- A way for another user or program to make the helper act without the user's password prompt

## Supported versions

Only the latest release receives fixes.
