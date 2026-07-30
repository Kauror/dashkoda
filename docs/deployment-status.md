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
- the deployed build still predates the legal-work feed, so it shows only
  truthful empty states and **no business data source is connected there**.

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

## What the legal-work feed changes here

It is the first module carrying **real Chamber information**, which changes the
risk picture even though it changes nothing operationally in this repository.

Still true: no server, Cloudflare, DNS or tunnel change; no deployment; no
schedule installed. The 07:00 job exists only as a script template.

Newly required for a deployment that actually syncs, via the **MVP public-link
route**:

- one environment variable, `OIGUSLOOME_PUBLIC_URL`, held only in the server's
  own environment file and treated as a credential;
- a host schedule on `Europe/Tallinn`, created by an administrator.

That is the whole list. **No Entra application, Graph credential, rclone, Power
Automate, webhook or upload endpoint is required**, and no new volume is needed
because this route keeps no permanent copy of the workbook.

The Microsoft Graph route remains available and still needs its five variables,
an Entra application with the read-only `Files.Read.All` application permission
and tenant admin consent — but it is optional.

**Neither route has completed live acceptance, and neither schedule is
installed.**

- Graph: no credentials existed during development, so that collector is
  verified against mocked transports only.
- Public link: the download, URL handling, XLSX validation and temporary-file
  cleanup **have** been verified against the live link, across more than one
  published revision of the workbook. The end-to-end import has not completed. It
  needs two things that are outside this repository: a PostgreSQL instance to
  publish into, and a workbook published with its `tbl_oigusloome` Excel Table
  intact — uploading through Excel Online strips it. See
  [legal-work-feed.md](legal-work-feed.md).

The exact post-deployment commands are in
[legal-work-feed.md](legal-work-feed.md).

The known risk below now matters more: the pilot no longer holds only empty
states, so Cloudflare Access in front of the tunnel should be settled before
this is treated as a production service.

## What PR-05 changed here

Nothing operationally. PR-05 adds the source, artifact, import-registry and
audit models and a private storage location for original files. It adds no
importer, no real data, no scheduled job and no server-side change. Its only
deployment-adjacent requirement is a persistent private directory for source
artifacts; Compose provides one through the `source_artifacts` named volume, and
`SOURCE_ARTIFACT_ROOT` must be set in any non-Compose deployment.
