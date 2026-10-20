from applicant_support.evals.dataset import DATASET_NAME, build_seed_dataset
from applicant_support.evals.hosted import create_dataset_client, sync_seed_dataset


def main() -> None:
    seed = build_seed_dataset()
    print(
        "Warning: matching named seed cases will be updated; differently named "
        "hosted and trace-derived cases will be retained."
    )
    with create_dataset_client() as client:
        detail = sync_seed_dataset(client)
    print(f"Dataset: {DATASET_NAME}")
    print(f"Dataset ID: {detail['id']}")
    print(f"Seed case count: {len(seed.cases)}")


if __name__ == "__main__":
    main()
