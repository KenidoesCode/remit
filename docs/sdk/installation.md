# Installation

```bash
npm install remit-sdk
```

```bash
npm i -g remit-sdk      # the `remit` CLI
```

## Why the package is not called `remit`

`remit` on npm is an unrelated microservices library (v2.4.1, four active
maintainers, `github.com/jpwilliams/remit`). It is not available and it is not
abandoned, so the install command in this project is `remit-sdk`.

Publishing under a name we do not own is not possible, and printing
`npm install remit` on a website would be a command that fails for every person
who runs it. **The CLI binary is still `remit`**, so every command in these docs
reads the way it should.

## Requirements

| | |
|---|---|
| Node.js | **>= 18.17.0** |
| Platforms | Windows, macOS, Linux |
| Architectures | x64, arm64 |
| Runtime dependencies | **none** |

18.17 is the floor because the SDK uses built-in `fetch`, `AbortController` and
`crypto.subtle` rather than shipping polyfills. `remit doctor` checks your
version and says so plainly if it is too old.

The CLI touches the filesystem only through `node:fs` and `node:path`, never
shells out, and never assembles a path with `/`. There are no platform-specific
branches in the source.

**What has actually been run:** Linux x64 on Node 22 during development, and
CI runs install + build + test + pack on `ubuntu-latest`, `macos-latest` and
`windows-latest` across Node 18, 20 and 22. Anything beyond that is a
reasonable expectation rather than a measurement.

## Verify the install

```bash
remit doctor
```

```
REMIT DOCTOR
------------

  ok   Node.js  v22.22.2
  ok   SDK  remit-sdk 0.1.0
  ok   fetch  available
  ok   API reachable  https://remit-vvug.onrender.com
  ok   Protocol compatible  server 1.0, SDK speaks 1.x
  ok   Identity  session issued by the server

REMIT IS READY.
```

Every line is something that was checked on this run. Nothing is inferred from
the fact that the import succeeded.

## Pointing at your own deployment

```bash
export REMIT_BASE_URL=http://127.0.0.1:8099
remit doctor
```

or per command: `remit --url http://127.0.0.1:8099 doctor`.

To run REMIT locally:

```bash
git clone https://github.com/KenidoesCode/remit
cd remit
pip install -r requirements.txt
python -m uvicorn remit.api:api --port 8099
```
