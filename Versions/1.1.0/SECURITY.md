# Security Policy

## Supported version

Only the latest version in the repository root receives security updates.
Files under `Versions/` are preserved snapshots and may contain fixed issues.

## Reporting a vulnerability

Do not publish exploitable details in a public issue. Use the repository's
Security tab and open a private security advisory.

Include the affected version, reproduction conditions, impact, and the
smallest safe proof of concept needed to understand the issue.

## Protocol limitation

This project uses ordinary NTP over UDP. It does not provide cryptographic
server authentication and must not be used as a trusted time authority for
security-sensitive decisions. Use an NTS-capable implementation when
authenticated time is required.
