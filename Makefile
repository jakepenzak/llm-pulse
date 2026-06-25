.PHONY: help venv tests coverage run

help:
	@printf "Usage: make <target> \n\n"
	@printf "Targets:\n"
	@printf "   venv            Create/sync virtual environment and install hooks.\n"
	@printf "   tests           Run tests with pytest.\n"
	@printf "   coverage        Serve coverage report at http://localhost:8080.\n"
	@printf "   help            Show this help message.\n"

venv:
	@echo "Syncing venv based on lock file..."
	@uv sync --all-extras --all-groups --frozen
	@uv run pre-commit install

tests:
	@echo "Running tests with pytest..."
	@uv run pytest tests/

coverage:
	@echo "Serving coverage report at http://localhost:8080 ..."
	@uv run python -m http.server 8080 -d tests/reports/htmlcov
