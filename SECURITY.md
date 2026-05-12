# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 3.5.x   | :white_check_mark: |
| 3.0.x   | :white_check_mark: |
| < 3.0   | :x:                |

## Reporting a Vulnerability

We take security vulnerabilities seriously. If you discover a vulnerability in MindSave, please report it responsibly.

**How to report:**

1. **Do not** open a public GitHub issue for security vulnerabilities.
2. Email the maintainer at [GitHub security advisory](https://github.com/Ringlingo/mindsave/security/advisories/new), or
3. Use [GitHub's private vulnerability reporting](https://github.com/Ringlingo/mindsave/security) feature.

**What to include:**

- Description of the vulnerability
- Steps to reproduce
- Potential impact
- Suggested fix (if any)

**Response timeline:**

- Acknowledgment within 48 hours
- Initial assessment within 5 business days
- Fix or mitigation within 30 days (severity-dependent)

## Security Measures

### Built-in Protections

MindSave includes several safety mechanisms:

- **HR-001**: No destructive file operations (rm -rf, format, etc.)
- **HR-002**: No system file modifications
- **HR-003**: No unauthorized external network calls
- **HR-004**: No credential or secret exposure in snapshots
- **HR-005**: No arbitrary code execution from snapshot data

### Automated Scanning

- [ ] Dependabot enabled for dependency scanning
- [ ] CodeQL analysis for Python and TypeScript
- [ ] Regular `npm audit` and `pip audit` checks

### Data Handling

- All snapshot data is stored **locally** — no data leaves the machine.
- No API keys, tokens, or secrets are stored in snapshots.
- Failure graph data is scoped to `project` or `global` with filesystem-level isolation.

## Known Security Considerations

1. **Prompt injection**: MindSave relies on AI agent compliance with CLAUDE.md/SKILL.md rules. Deterministic runtime hooks (planned for v3.6/v4.0) will enforce these rules programmatically.
2. **Local path exposure**: Snapshot files may contain local filesystem paths. Do not share `.mindsave/` directories publicly without reviewing contents.
3. **No encryption**: Snapshots are stored as plain text (YAML + Markdown). Sensitive projects should use filesystem-level encryption.
