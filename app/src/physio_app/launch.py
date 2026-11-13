"""`physio-app`: start the Streamlit server on this machine only."""

import sys
from pathlib import Path

OPTIONS = [
    "--server.address=localhost",
    "--server.headless=false",
    "--browser.gatherUsageStats=false",
    "--theme.base=dark",
    "--theme.primaryColor=#3987e5",
    "--theme.backgroundColor=#141413",
    "--theme.secondaryBackgroundColor=#1f1f1d",
    "--theme.textColor=#f0efec",
    "--client.toolbarMode=minimal",
]


def main() -> None:
    from streamlit.web import cli

    sys.argv = [
        "streamlit",
        "run",
        str(Path(__file__).with_name("main.py")),
        *OPTIONS,
        *sys.argv[1:],
    ]
    sys.exit(cli.main())
