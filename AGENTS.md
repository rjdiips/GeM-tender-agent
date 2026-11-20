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