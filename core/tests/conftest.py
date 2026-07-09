def pytest_addoption(parser):
    parser.addoption(
        "--update-goldens",
        action="store_true",
        default=False,
        help="Regenerate golden op-dump files instead of comparing against them.",
    )
