# Deploying managed settings

The file must be writable by administrators only. Navin reads it on every
turn (cached by modification time), so updates apply without a restart.

## Linux

```bash
sudo install -d -m 755 /etc/navin
sudo install -m 644 -o root -g root managed-settings.json /etc/navin/managed-settings.json
```

Keep managed hook scripts in `/etc/navin/hooks/`, owned by root, mode 755.
Configuration tools (Ansible, Puppet, Chef, Salt) only need to place that one
file.

## macOS

Path: `/Library/Application Support/Navin/managed-settings.json`, owned by
`root:wheel`, mode 644.

- Manual: `sudo mkdir -p "/Library/Application Support/Navin"` then copy the
  file.
- MDM (Jamf, Kandji, Intune for Mac): deploy the JSON as a file payload or with
  a post-install script in a signed package. A configuration profile cannot
  write arbitrary JSON, so use a package or script payload that writes the
  file.

## Windows

Path: `%ProgramData%\Navin\managed-settings.json` (normally
`C:\ProgramData\Navin\managed-settings.json`).

- Restrict the folder ACL: Administrators and SYSTEM full control, Users read.
- Group Policy: a Files preference item (Computer Configuration, Preferences,
  Windows Settings, Files) copies the file from a protected share.
- Intune: a PowerShell platform script that writes the file and sets the ACL.

## Testing before rollout

Set `NAVIN_MANAGED_SETTINGS=/path/to/draft.json` for a test user. This path is
read before the system paths, so never leave it set in production profiles.

## MDM notes

- Ship one JSON per policy group; do not merge per user.
- Validate with `python -m json.tool` in the pipeline: a broken file triggers
  the lockdown policy on every machine (fail closed).
- Version the file in a repository and deploy from there.
- After rollout, check one machine for the "Organization policy in force"
  line in the system prompt.
