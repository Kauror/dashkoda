# Deployment status

This file exists because the rest of the documentation used to describe
`dash.orgusaar.ee` as merely planned, and that is no longer true. It records
what actually runs, what this repository owns, and what is still outstanding.

## What is running

DashKoda runs as a **development/pilot deployment**:

- reachable at `https://dash.orgusaar.ee`;
- Docker on Unraid, two application containers (`web` and `db`);
- PostgreSQL data persists on the host, outside the container;
- an existing Cloudflare Tunnel fronts it;
- the dashboard shows only truthful empty states;
- **no business data source is connected.**

## What this repository owns

Only the application: Django code, the Compose application stack, the image, CI
and the documentation here.

It does **not** own, configure or change:

- Cloudflare, its tunnel, its routes or its access rules;
- DNS;
- the Unraid host, its shares or its mounts;
- `cloudflared`, which is managed separately from the DashKoda Compose stack.

No tunnel token, Cloudflare credential, production `.env`, PIN, PIN hash or
other production value is ever committed. The checked-in `.env.example`
contains placeholders only.

## What is not done

The earlier deployment is a **sequencing deviation**. It does not mean the
planned operations milestone (PR-09) is complete. Still outstanding:

- backup automation;
- a tested restore procedure;
- rollback tooling;
- Unraid deployment configuration held in this repository;
- the full operations runbook.

Until those exist, the deployment should be treated as a pilot rather than as a
hardened production service.

## Known risk

**The deployed environment is currently also the development/pilot
environment.** There is no separate staging. A change reaches the same place
people are looking at. This is accepted for now because the dashboard holds no
business data, but it stops being acceptable once real Chamber data is
connected, and it should be resolved before that point.

## What PR-05 changed here

Nothing operationally. PR-05 adds the source, artifact, import-registry and
audit models and a private storage location for original files. It adds no
importer, no real data, no scheduled job and no server-side change. Its only
deployment-adjacent requirement is a persistent private directory for source
artifacts; Compose provides one through the `source_artifacts` named volume, and
`SOURCE_ARTIFACT_ROOT` must be set in any non-Compose deployment.
