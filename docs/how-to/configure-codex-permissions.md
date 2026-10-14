# Configure Codex workspace permissions

`basicly install` projects a shared `basicly-workspace` policy into
`.codex/config.toml`. Commit that file with the repository. The policy extends
Codex's `:workspace` protections, denies dotenv reads, and defaults to
`on-request` with `auto_review`. It leaves network permissions inherited.
Existing explicit profile, approval and reviewer choices are preserved.

The file also contains a repository-specific profile, named
`basicly-workspace-<identity>`, extending the shared policy. Its identity is
created once and survives upgrades, clones and moves. A fresh installation
selects this profile. It contains no machine-specific paths.

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

Repeat registration on each machine after cloning. The installer resolves the
main checkout through Git and grants its `<repo>.worktrees` directory to that
repository's profile. Running from a linked checkout registers the same main
sibling directory. Future worktrees under it need no additional registration.
Two different repositories use different profile identities and do not share
their sibling grants. Two local clones of the same shared identity are the same
logical repository and can register their respective sibling directories.

Registration writes only the named profile's path grant in `$CODEX_HOME/config.toml`,
or `~/.codex/config.toml` when `CODEX_HOME` is unset. It preserves existing user
defaults and unrelated settings, creates private backups of changed user files,
and supplies `:workspace` as the user default only when none exists. Ordinary
installation does not modify the user configuration. No global review supplement
or command approval rules are installed.

If the project explicitly selects `:workspace`, `:read-only`, or another profile,
basicly preserves that selection. Local registration does not switch it. Select
the repository-specific profile to use its sibling grants. Legacy sandbox settings
and conflicting settings in the shared base are refused before registration;
reconcile them rather than combining the old sandbox system with profiles.

Codex loads trusted project configuration. A clone may need the client's repository
trust confirmation. Client and managed settings can override the declared policy.
A configuration check establishes the files, not an existing session's settings;
inspect that session's persisted `turn_context` to verify the selected profile and
reviewer. Changing the files does not prove that a live session adopted them.

Basicly's default headless Codex runner inherits the project's policy. Explicit
runner overrides remain available. Decision-only runs select Codex's read-only
profile and disallow approval escalation.
