# SOL-Trader Cursor Rules

This directory contains Cursor AI rules to help with code navigation, understanding, and generation for the SOL-Trader project.

## Rules Overview

### Always Applied Rules

These rules are automatically applied to every AI request:

- **project-architecture.mdc** - Project structure, multi-account architecture, file naming conventions

### Context-Specific Rules

These rules are applied based on file types (globs):

- **python-style.mdc** (`*.py`) - Python code style, imports, docstrings, logging patterns
- **testing.mdc** (`tests/**/*.py`) - Testing patterns, mocks, fixtures, assertions
- **configuration.mdc** (`config/*.yaml`, `config/*.py`) - Config structure, validation, risk parameters
- **data-files.mdc** (`data/*.json`, `data/*.csv`) - State files, metrics, trade history structure

### Manual/Description-Based Rules

These rules are fetched based on semantic relevance:

- **trading-logic.mdc** - Trading calculations, fees, limit orders, risk management
- **logging.mdc** - Logging conventions, per-account logs, Helsinki timezone
- **documentation.mdc** (`docs/*.md`, `*.md`) - Documentation guidelines, risk warnings

## How to Use

The Cursor AI will automatically load and apply these rules based on:
1. File types you're working with (via globs)
2. Always-applied rules for project context
3. Semantic search when you ask trading/logging/docs questions

## Updating Rules

When the project structure or patterns change:
1. Update the relevant `.mdc` file
2. Ensure file references use `[filename](mdc:path/to/file)` format
3. Keep frontmatter metadata accurate (alwaysApply, globs, description)

## Rule Categories

| Rule | Type | Purpose |
|------|------|---------|
| project-architecture.mdc | Always Applied | Project overview, isolation, file conventions |
| python-style.mdc | File Type (*.py) | Code style, imports, error handling |
| trading-logic.mdc | Description-Based | Financial calculations, fees, risk mgmt |
| testing.mdc | File Type (tests/) | Test patterns, mocks, fixtures |
| configuration.mdc | File Type (config/) | Config structure, env vars, validation |
| data-files.mdc | File Type (data/) | State persistence, file formats |
| logging.mdc | Description-Based | Log patterns, timezone, rate limiting |
| documentation.mdc | File Type (*.md) | Docs structure, warnings, examples |

