# Cartograph

Natural-language querying of complex data warehouses with schema-graph retrieval and explainable join-path selection.

## Running it locally

Requires Docker Desktop with the WSL2 backend.

    ./scripts/bootstrap.sh
    docker compose up

`bootstrap.sh` writes a `.env` with generated passwords if one does not
already exist, and never overwrites an existing file. The repository
contains no credentials of any kind, in the working tree or in its
history, so this step is what makes `docker compose up` work on a clean
clone.

Once up:

- Frontend: http://localhost:3000
- API: http://127.0.0.1:8000/api/v1/health and /api/v1/ready

