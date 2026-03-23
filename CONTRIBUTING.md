# CONTRIBUTING.md

## Welcome!

Thank you for considering contributions! I prioritize code quality, testing, and clear communication.

## Quick Start
1. **Fork** this repository (top-right button)
2. Clone your fork: `git clone git@github.com:YOUR_FORK/REPO_NAME.git`
3. Create feature branch: `git checkout -b feat/description`
4. Commit → Test → Push → **Submit Pull Request**

## Commit Message Standard
**[Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/)**:

```
type[(scope)]: short summary (max 50 chars)

Detailed body text (wrap at 72 chars), explaining *what* and *why*.

Closes #123
```

**Types**:
- `feat`: New feature
- `fix`: Bug fix
- `docs`: Documentation
- `style`: Formatting
- `refactor`: Restructure
- `perf`: Performance improvement
- `test`: Tests
- `chore`: Maintenance

**Examples**:
```
feat(ui): add zone preview canvas
fix(tracker): prevent ID collision on reboot
docs(readme): update hardware requirements
```

## Code Standards
Run these before committing:
```
pre-commit run --all-files
# or individually:
black .
ruff check --fix .
mypy .
```

**Enforced by CI.**

## Testing
- **Unit**: 80% coverage
- **Integration**: Full end-to-end workflows
- **Manual**: Test on target hardware (RPi 5, etc.)

All PRs must pass GitHub Actions workflow.

## Pull Request Best Practices
- **Single purpose**: One feature/fix per PR
- **Small**: <400 lines changed
- **Self-contained**: No "WIP" without reviewer request
- **Template**: Use PR description template

**Required Labels**:
```
Status: Ready to merge
Tests: ✅ Passed
Docs: ✅ Updated
```

**Checklist**:
```
- [ ] Pre-commit passes
- [ ] Tests pass (local + CI)
- [ ] Docs updated
- [ ] CHANGELOG.md entry
```

## Issues & Questions
- **Bugs**: Repro steps + expected/actual
- **Features**: Use case + alternatives considered
- **Questions**: Check existing issues/discussions first

## Support Channels
- GitHub Issues (bugs/features)
- GitHub Discussions (ideas/help)

---