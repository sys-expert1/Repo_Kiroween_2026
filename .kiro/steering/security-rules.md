---
inclusion: always
---
# Security rules

- Run untrusted repository code only in the restricted sandbox: non-root, CPU, memory, process and
  time limits, network disabled by default, restricted mounts.
- Never mount the user's home directory or the Docker socket.
- Docker is a practical boundary, not a guarantee. Do not overstate it.
- Validate local paths: resolve symlinks, reject `..`, enforce allowed roots. The browser never
  reads local paths; the local backend does.
- Accept Git URLs only as `https://`, `ssh://` or `git@host:path`. Reject `file://`, `ext::`,
  leading `-` and whitespace.
- Do not run git or repository code during read-only inspection (hostile `.git/config`).
- Keep secrets out of URLs, logs, events and artifacts. Redact before logging.
- Quote and escape untrusted values passed to commands. Prefer argument lists over shell strings.
- Preserve uncommitted changes in a source repository. Never reset or clean it.
- Artifacts are scoped to the local application and recorded with checksums.
