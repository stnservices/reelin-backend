# Reelin Backend - Git Setup Commands

## Repository
- **URL**: https://github.com/stnservices/reelin-backend.git
- **Branch**: main

## Initial Push (First Time)

```bash
# Stage all files
git add .

# Commit
git commit -m "Initial commit - Reelin Backend API"

# Push to main
git push -u origin main
```

## Daily Workflow

```bash
# Check status
git status

# Pull latest changes
git pull origin main

# Stage changes
git add .

# Commit with message
git commit -m "Your commit message"

# Push changes
git push origin main
```

## Useful Commands

```bash
# View commit history
git log --oneline

# View remote info
git remote -v

# Create and switch to new branch
git checkout -b feature/your-feature

# Switch branches
git checkout main

# Merge branch
git merge feature/your-feature
```
