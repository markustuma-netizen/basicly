# Remove the generated Codex permission policy

Earlier versions of basicly projected a `basicly-workspace` profile, a
`basicly-read-only` judgment profile and managed command-prefix rules into a
repository. basicly no longer writes them. Codex runs on its own defaults, and
you choose your own permission settings.

`basicly install` and `basicly permissions-build` remove what an earlier version
wrote:

- the `basicly-workspace` and `basicly-read-only` profiles in `.codex/config.toml`;
- an old generated GUID profile, named `basicly-workspace-<32 hex digits>`;
- `default_permissions`, but only when it names one of those profiles;
- the generated `on-request` approval policy and `auto_review` reviewer, when a generated profile or selection exists;
- the managed rule lines in `.codex/rules/consent-floor.rules`.

The command deletes a file when nothing is left in it. It repeats without a
further change, and `basicly permissions-check` then reports nothing.

## What stays

Every other setting stays. This includes a profile you wrote yourself, a
`default_permissions` that names a Codex preset such as `:workspace` or
`:read-only`, consumer approval settings, `[features]` and a rule
line you added to `consent-floor.rules` yourself.

A file that basicly cannot change safely is refused and left alone. The check
names it. Reconcile that file by hand.

## The machine-local configuration

```sh
basicly permissions-build --codex-user
basicly permissions-check --codex-user
```

The flag also cleans `$CODEX_HOME/config.toml`, or `~/.codex/config.toml` when
`CODEX_HOME` is unset. basicly copies the file into a private
`permission-backups` directory beside it before it changes it. Without the flag,
the user configuration is not read and not changed.

## Choose your own policy

Codex applies its own default when `default_permissions` is absent. To pin a
policy, write your own selection or your own profile into `.codex/config.toml`
and commit it. basicly leaves it in place.

A decision-only dispatch is the one exception. basicly passes
`-c default_permissions=":read-only"` on the argv of that run, so a judging run
cannot write. That is a Codex preset, not a basicly profile, and it is not
written to any file.
