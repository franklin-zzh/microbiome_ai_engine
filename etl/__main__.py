"""python -m etl 入口（委托 etl.cli.main）。"""
import sys

from etl.cli import main

if __name__ == "__main__":
    sys.exit(main())
