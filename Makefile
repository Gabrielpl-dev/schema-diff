.PHONY: test lint check

test:
	python3 -m unittest discover -s tests

lint:
	sh scripts/lint.sh

check: lint test
