"""Explicit supplier-policy upload helper; no network call occurs on import."""

from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
POLICIES_DIRECTORY = REPOSITORY_ROOT / "policies"


def find_policy_pdfs() -> list[Path]:
    """Return the local policy PDFs that are ready for upload."""
    return sorted(POLICIES_DIRECTORY.glob("SUP*_policy.pdf"))


def upload_policies(policy_paths: list[Path]) -> None:
    """Upload policy PDFs to OCI Object Storage in a future implementation."""
    # TODO: Load OCI configuration and upload each file through OCIStorageClient.
    raise NotImplementedError(f"Object Storage upload is pending for {len(policy_paths)} files.")


if __name__ == "__main__":
    raise SystemExit("Policy upload is a placeholder; implement upload_policies before use.")
