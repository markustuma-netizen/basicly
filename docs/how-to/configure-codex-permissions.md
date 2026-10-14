# Configure Codex workspace permissions

`basicly install` projects a shared `basicly-workspace` policy into
`.codex/config.toml`. Commit that file with the repository. The policy extends
Codex's `:workspace` protections, denies dotenv reads, and defaults to
`on-request` with `auto_review`. It leaves network permissions inherited.
Existing explicit profile, approval and reviewer choices are preserved.

There is one named permission profile: `basicly-workspace`. Old generated GUID
profiles are migrated into it. Consumer profile choices remain unchanged.

To keep automatic access to sibling worktrees, register the local path as part
of installation:

```sh
basicly install --codex-user
basicly permissions-check --codex-user
```

For an existing installation:

```sh
basicly permissions-build --codex-user
basicly permissions-check --codex-user
```

Repeat registration on each machine after cloning or renaming a checkout. The
installer resolves the main checkout through Git and adds its sibling
`<repo>.worktrees` directory as a relative `workspace_roots` entry in the project's
profile. Linked checkouts resolve the same main sibling directory. Future worktrees
under it need no additional registration. Each repository has its own project
entry; the global profile contains no union of unrelated sibling directories.

Registration makes the named policy discoverable in `$CODEX_HOME/config.toml`,
or `~/.codex/config.toml` when `CODEX_HOME` is unset. It preserves existing user
defaults and unrelated settings, creates private backups of changed user files,
and supplies `:workspace` as the user default only when none exists. Ordinary
installation does not modify the user configuration. No global review supplement
or command approval rules are installed. A legacy profile with consumer additions
or another repository's grant is refused instead of silently deleting those fields.

If the project explicitly selects `:workspace`, `:read-only`, or another profile,
basicly preserves that selection. Local registration does not switch it. Select
`basicly-workspace` to use its sibling grants. Legacy sandbox settings
and conflicting settings in the shared base are refused before registration;
reconcile them rather than combining the old sandbox system with profiles.

Codex loads trusted project configuration. A clone may need the client's repository
trust confirmation. Client and managed settings can override the declared policy.
A configuration check establishes the files, not an existing session's settings;
inspect that session's persisted `turn_context` to verify the selected profile and
reviewer. Changing the files does not prove that a live session adopted them. A loaded Codex
thread can retain its earlier configuration and ignore resume overrides. Select
`basicly-workspace` in the permission picker and verify a sibling write and a local
HTTP server in that thread. If selection rejects a profile that configuration
reading lists, unload the idle thread and resume the same thread with the profile;
then repeat the runtime checks. Do not keep trying broader permission modes.

Basicly's default headless Codex runner inherits the project's policy. Explicit
runner overrides remain available. Decision-only runs select Codex's read-only
profile and disallow approval escalation.
