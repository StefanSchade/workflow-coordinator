# Architecture

`workflow_coordinator` is a small `src`-layout Python package. Its operator owns
deterministic admission, serial workflow progression, Git observation and
bounded mechanical commits, evidence execution, persistence, and stop routing.
The provider-neutral execution port separates those semantics from the Codex
CLI adapter; consumers own task inputs, repository policy, workflow profiles,
and authorization.

The package-owned A3 operating contract is loaded through `importlib.resources`
and cannot be selected or replaced by a consumer. Durable consumer evidence and
repository mutations remain governed by the admitted consumer inputs. One
WorkItem mutates one repository.

For self-development, the accepted runtime and development repositories remain
separate:

```text
accepted Runtime WFC N  --operates on-->  Development WFC N+1
accepted Runtime WFC N  --never mutates--> Runtime WFC N
```

The runtime executable, repository, Git state, and execution artifacts are not
development payload. WFC may create only bounded local commits admitted for the
development repository. A maintainer reviews and accepts those commits and
alone owns any push and any later, separate runtime promotion. No current
Coordinator-owned or Coordinator-verified OS/environment containment boundary
is claimed.

The [standalone authority index](README.adoc) is the authoritative
classification of current authority, provenance, historical material, and
planning evidence. This architecture document does not duplicate that index.
For active unimplemented work, use the
[active-work manifest](backlog/_open.adoc), not historical concepts or reviews.
