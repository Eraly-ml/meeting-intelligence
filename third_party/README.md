# Scriberr source

The application in the repository root extends the existing Scriberr fork in
`https://github.com/Eraly-ml/meeting-intelligence`. Its existing history and MIT
license are retained.

A separate upstream reference checkout was cloned to `third_party/scriberr/` from
`https://github.com/rishikanthc/Scriberr.git`, at commit
`bdb8838b8b9e4a58e74297f6ed2d0acb4c341c4f`. That checkout is ignored to avoid
duplicating the complete application inside this repository. Recreate it with:

```sh
git clone https://github.com/rishikanthc/Scriberr.git third_party/scriberr
git -C third_party/scriberr checkout bdb8838b8b9e4a58e74297f6ed2d0acb4c341c4f
```

See the root `LICENSE` for the retained Scriberr copyright and MIT terms.
Provisioned inference binaries, model weights and their own licenses remain local
under ignored `.local/` and `models/` directories; they are not redistributed by
this source commit.
