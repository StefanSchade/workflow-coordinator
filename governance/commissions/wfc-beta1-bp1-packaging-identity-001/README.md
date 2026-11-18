# BP1 Commission authoring

The robust@v1 phase topology was rendered from program commit
`8b5b7c6320cc67860146aceb16955c893d9f6e74`. This is a manually finalized
Commission, not a byte-exact `commission render` bundle: the closed
`repository-standard@v1` basis excludes `pyproject.toml` and `.gitattributes`,
which the maintainer-authorized BP1 packaging scope may require. The local
RepositoryPolicy and WorkItem/worker scopes explicitly include these paths,
README and the source-commit resource; all other rendered review and evidence
semantics remain intact. The local policy retains the same action, Hop and
`task-check` limits. Canonical Runtime preflight at preparation validates the
committed bundle; `commission check` is specific to immutable rendered
Intent bundles and is not applicable to this manually finalized one.
