# Reelin V2 - Production Deployment & Migration Plan

## Overview

This document outlines the complete process for deploying Reelin V2 to DigitalOcean App Platform and migrating data from the old application.

---

## Pre-Deployment Checklist

### 1. DigitalOcean Setup

- [ ] Create DigitalOcean account (if not existing)
- [ ] Create Spaces bucket: `reelin-prod` in `fra1` region
- [ ] Generate Spaces access keys (API > Spaces Keys)
- [ ] Enable CDN on Spaces bucket
- [ ] Configure CORS on Spaces bucket:
  ```json
  {
    "CORSRules": [{
      "AllowedOrigins": ["https://admin.reelin.ro", "https://api.reelin.ro"],
      "AllowedMethods": ["GET", "PUT", "POST", "DELETE"],
      "AllowedHeaders": ["*"],
      "MaxAgeSeconds": 3600
    }]
  }
  ```

### 2. Domain Setup

- [ ] Add domain `reelin.ro` to DigitalOcean
- [ ] Configure DNS records:
  ```
  A     @              -> DO App Platform IP
  A     api            -> DO App Platform IP
  A     admin          -> DO App Platform IP
  CNAME www            -> reelin.ro
  ```

### 3. Stripe Setup

- [ ] Create Stripe account (production mode)
- [ ] Create Products:
  - `Reelin Pro Monthly` - €4.99/month
  - `Reelin Pro Yearly` - €29.99/year
- [ ] Copy Price IDs for both products
- [ ] Note API keys (publishable + secret)
- [ ] Webhook will be configured after deploy

### 4. Firebase Setup (Push Notifications)

- [ ] Create Firebase project
- [ ] Enable Cloud Messaging
- [ ] Download service account JSON
- [ ] Update mobile app with Firebase config

### 5. Generate Security Keys

```bash
# Generate SECRET_KEY
openssl rand -hex 32

# Generate JWT_SECRET_KEY
openssl rand -hex 32

# Generate ADMIN_PASSWORD
openssl rand -base64 16
```

---

## Deployment Steps

### Phase 1: Initial Deployment

#### Step 1.1: Deploy Application

```bash
# Install doctl CLI
brew install doctl  # macOS
# or snap install doctl  # Linux

# Authenticate
doctl auth init

# Create the app
cd reelin-backend
doctl apps create --spec deploy/app.yaml
```

#### Step 1.2: Configure Secrets

In DO Dashboard (Apps > reelin > Settings > App-Level Environment Variables):

| Variable | Value |
|----------|-------|
| `SECRET_KEY` | (generated) |
| `JWT_SECRET_KEY` | (generated) |
| `ADMIN_PASSWORD` | (generated) |
| `DO_SPACES_KEY` | (from Spaces) |
| `DO_SPACES_SECRET` | (from Spaces) |
| `STRIPE_API_KEY` | sk_live_xxx |
| `STRIPE_WEBHOOK_SECRET` | (after webhook setup) |
| `STRIPE_MONTHLY_PRICE_ID` | price_xxx |
| `STRIPE_YEARLY_PRICE_ID` | price_xxx |
| `FIREBASE_CREDENTIALS_JSON` | {"type":"service_account"...} |

#### Step 1.3: Verify Deployment

```bash
# Check app status
doctl apps list

# View logs
doctl apps logs <app-id> --type=run

# Test health endpoint
curl https://api.reelin.ro/api/v1/health
```

#### Step 1.4: Run Database Migration

The `db-migrate` job runs automatically on deploy. Verify:

```bash
# Check job status
doctl apps list-deployments <app-id>
```

#### Step 1.5: Run Production Seed

```bash
# Trigger seed job manually
doctl apps create-deployment <app-id> --run-job seed-production

# Or via SSH/console
doctl apps console <app-id> backend
python -m scripts.seed_production
```

---

### Phase 2: Stripe Webhook Setup

#### Step 2.1: Create Webhook Endpoint

1. Go to [Stripe Dashboard > Webhooks](https://dashboard.stripe.com/webhooks)
2. Click "Add endpoint"
3. Configure:
   - **URL**: `https://api.reelin.ro/api/v1/stripe/webhook`
   - **Events**:
     - `checkout.session.completed`
     - `customer.subscription.created`
     - `customer.subscription.updated`
     - `customer.subscription.deleted`
     - `invoice.paid`
     - `invoice.payment_failed`

#### Step 2.2: Copy Webhook Secret

1. After creating, click on the endpoint
2. Click "Reveal" under Signing secret
3. Copy the `whsec_xxx` value
4. Update `STRIPE_WEBHOOK_SECRET` in DO App Platform

#### Step 2.3: Test Webhook

```bash
# Using Stripe CLI
stripe listen --forward-to https://api.reelin.ro/api/v1/stripe/webhook

# Trigger test event
stripe trigger checkout.session.completed
```

---

### Phase 3: Data Migration

#### Step 3.1: Prepare Old Database Access

Ensure you can connect to the old database:

```bash
# Test connection
psql postgresql://user:pass@old-host:5432/old_reelin -c "SELECT count(*) FROM users"
```

#### Step 3.2: Review Field Mappings

Edit `scripts/migrate_from_old.py` and adjust:

1. **Table names** - Update SQL queries if your tables have different names
2. **Field mappings** - Update `USER_MAPPING`, `EVENT_MAPPING`, etc.
3. **Value mappings** - Update `EVENT_STATUS_MAPPING`, `USER_ROLE_MAPPING`, etc.

#### Step 3.3: Dry Run

```bash
# Set old database URL
export OLD_DATABASE_URL="postgresql://user:pass@old-host:5432/old_reelin"

# Run dry migration
python -m scripts.migrate_from_old --dry-run
```

Review output for any issues.

#### Step 3.4: Full Migration

```bash
# Backup new database first!
doctl databases backups create <db-id>

# Run migration
python -m scripts.migrate_from_old

# Or migrate specific entities
python -m scripts.migrate_from_old --only users,events
```

#### Step 3.5: Verify Migration

```bash
# Check counts
psql $DATABASE_URL -c "SELECT 'users' as table, count(*) FROM user_accounts UNION ALL SELECT 'events', count(*) FROM events UNION ALL SELECT 'catches', count(*) FROM catches"

# Verify admin can login
curl -X POST https://api.reelin.ro/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@reelin.ro","password":"xxx"}'
```

---

### Phase 4: Post-Migration

#### Step 4.1: Update Mobile App

1. Update API URL to `https://api.reelin.ro`
2. Update Stripe publishable key
3. Build and deploy to App Store / Play Store

#### Step 4.2: DNS Cutover

If changing from old domain:

1. Lower TTL to 300 seconds (5 min) 24h before
2. Update DNS records to point to new infrastructure
3. Monitor for errors

#### Step 4.3: Decommission Old System

1. Keep old database running for 30 days (rollback)
2. Set up redirect from old URLs if different
3. After 30 days, archive and delete old infrastructure

---

## Migration Order & Dependencies

```
┌─────────────────────────────────────────────────────────────────┐
│                    MIGRATION ORDER                               │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  1. USERS ─────────────────────────────────────────────────────►│
│     └── Creates user_id_map (old_id -> new_id)                  │
│                                                                  │
│  2. EVENTS ────────────────────────────────────────────────────►│
│     └── Depends on: users (created_by_id)                       │
│     └── Creates event_id_map (old_id -> new_id)                 │
│                                                                  │
│  3. ENROLLMENTS ───────────────────────────────────────────────►│
│     └── Depends on: users, events                               │
│                                                                  │
│  4. CATCHES ───────────────────────────────────────────────────►│
│     └── Depends on: users, events                               │
│                                                                  │
│  5. SUBSCRIPTIONS ─────────────────────────────────────────────►│
│     └── Depends on: users                                       │
│     └── Preserves Stripe customer_id for continuity             │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

---

## Rollback Plan

### If Deployment Fails

```bash
# Rollback to previous deployment
doctl apps create-deployment <app-id> --rollback

# Check logs
doctl apps logs <app-id> --type=run --follow
```

### If Migration Fails

```bash
# Restore database from backup
doctl databases backups restore <db-id> <backup-id>

# Re-run seed
python -m scripts.seed_production

# Retry migration with fixes
python -m scripts.migrate_from_old
```

### Full Rollback to Old System

1. Revert DNS records
2. Re-enable old application
3. Investigate and fix issues
4. Retry migration

---

## Monitoring & Alerts

### Set Up Monitoring

1. **DigitalOcean Monitoring** (included)
   - CPU, Memory, Disk alerts
   - Enable in Apps > Insights

2. **Uptime Checks**
   - Add uptime check for `https://api.reelin.ro/api/v1/health`
   - Alert on failure

3. **Error Tracking** (optional)
   - Set up Sentry: `SENTRY_DSN=xxx`
   - Or use DO App Platform logs

### Key Metrics to Watch

| Metric | Warning | Critical |
|--------|---------|----------|
| API Response Time | > 500ms | > 2s |
| Error Rate | > 1% | > 5% |
| Database Connections | > 80% | > 95% |
| Memory Usage | > 80% | > 95% |
| Celery Queue Length | > 50 | > 200 |

---

## Support Contacts

| Issue | Contact |
|-------|---------|
| DigitalOcean | support@digitalocean.com |
| Stripe | dashboard.stripe.com/support |
| Domain/DNS | Your registrar |

---

## Estimated Timeline

| Phase | Duration |
|-------|----------|
| Pre-deployment setup | 2-4 hours |
| Initial deployment | 30 min |
| Stripe webhook setup | 15 min |
| Data migration (small) | 30 min - 1 hour |
| Verification & testing | 1-2 hours |
| **Total** | **4-8 hours** |

---

## Checklist Summary

- [ ] DigitalOcean account & Spaces configured
- [ ] Domain DNS configured
- [ ] Stripe products & prices created
- [ ] Firebase project configured
- [ ] Security keys generated
- [ ] App deployed to DO App Platform
- [ ] Secrets configured in dashboard
- [ ] Database migrations run
- [ ] Production seed run
- [ ] Stripe webhook configured
- [ ] Data migration completed
- [ ] Admin login verified
- [ ] API health check passing
- [ ] Mobile app updated
- [ ] DNS cutover complete
- [ ] Old system archived
