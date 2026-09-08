# Screenshot assets

Place workshop screenshots in this folder, grouped by module.

```text
docs/assets/images/
├── 03-deploy-infrastructure/
├── 04-enable-monitoring/
├── 05-configure-sre-agent/
├── 07-investigate-high-cpu/
├── 09-investigate-http-500/
├── 11-root-cause-analysis/
└── 12-agent-instructions/
```

## Conventions

* Capture at 1920x1080 or higher, then crop to the region of interest.
* Save as PNG with a descriptive kebab-case name, for example `sre-agent-incident-timeline.png`.
* Redact subscription IDs, tenant IDs, resource IDs, and email addresses before committing.
* Reference images with a caption so the site remains usable when images fail to load.

```markdown
![Azure SRE Agent incident timeline](../../assets/images/07-investigate-high-cpu/incident-timeline.png)
```

## Placeholder markers

Module pages contain `<!-- SCREENSHOT: ... -->` comments where a capture belongs. Replace the marker with the image reference once the screenshot exists. Do not commit references to files that are not in the repository, because `mkdocs build --strict` fails on missing assets.
