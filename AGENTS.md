# AGENTS.md

## Commit identity

Every commit in this repository is authored by **rjdiips <rjdiips@gmail.com>**

The machine's git config is intentionally left as it is: never change `user.name` or `user.email` in git config. Pass the identity on the commit command instead:

```bash
GIT_AUTHOR_NAME=rjdiips GIT_AUTHOR_EMAIL=rjdiips@gmail.com \
GIT_COMMITTER_NAME=rjdiips GIT_COMMITTER_EMAIL=rjdiips@gmail.com \
git commit -m "..."
```

- Never add `Co-Authored-By` trailers for AI models to commit messages.

## Type errors

Fix type-checker (pyright/Pylance) errors by correcting the logic or the type annotation — never by silencing the checker. Do not add `# type: ignore`, bare `assert x is not None`, `cast(...)`, or a reflexive `if x else ...` guard just to make the error disappear. If a value can be `None`, handle the `None` case for real; if the type is wrong, fix the annotation. Only suppress an error when it is a genuine false positive, and then add a one-line comment explaining why.