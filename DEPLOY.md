# Putting Daily Brief online

How the pieces fit:

- **GitHub Actions** runs `scraper.py` every hour and saves stories into Supabase.
- **Supabase** stores the stories and your login. Only accounts on the reader list can read anything.
- **Vercel** serves the page. You sign in once per device. The Refresh button asks GitHub to collect right away.

The GitHub repository holds only code, never articles, so it can be public (public repos get unlimited Actions minutes).

## 1. Supabase

1. Open the Supabase project to use, then **SQL Editor → New query**. Paste all of `supabase/schema.sql` and run it.
2. **Authentication → Sign In / Providers → Email**: turn **off** "Allow new users to sign up".
3. **Authentication → Users → Add user → Create new user**: your email and a strong password, with "Auto confirm" ticked.
4. Back in the SQL Editor, add yourself to the reader list (use the email from step 3):

   ```sql
   insert into public.dailybrief_readers (user_id)
   select id from auth.users where email = 'YOUR-EMAIL-HERE';
   ```

5. **Project Settings → API Keys**. You need three values:
   - the **Project URL** (`https://xxxx.supabase.co`)
   - the **publishable key** (`sb_publishable_…`), safe to put in the page
   - the **secret key** (`sb_secret_…`), which only goes into GitHub secrets and never into any file

6. Put the Project URL and publishable key into `config.js`.

## 2. GitHub

1. Push this folder to a new repository (the `.gitignore` keeps `data/` and `.venv/` out).
2. **Settings → Secrets and variables → Actions → New repository secret**:
   - `SUPABASE_URL`: the Project URL
   - `SUPABASE_SERVICE_KEY`: the secret key
3. **Actions → Collect news → Run workflow** to fill the database the first time. It takes 3–5 minutes.

GitHub turns off scheduled workflows in a repository with no commits for 60 days. You'll get an email; click **Enable workflow** on the Actions tab to switch it back on.

## 3. Refresh-button token

**GitHub → Settings → Developer settings → Fine-grained tokens → Generate new token**

- **Repository access:** only this repository
- **Permissions:** Actions, read and write
- **Expiry:** up to 1 year; set a reminder to renew it

## 4. Vercel

1. **Add New → Project →** import the GitHub repository. Framework preset: **Other**. Leave the build settings empty.
2. Before deploying, add these **Environment Variables**:
   - `SUPABASE_URL`: the Project URL
   - `SUPABASE_ANON_KEY`: the publishable key
   - `GH_REPO`: `owner/repo-name`
   - `GH_TOKEN`: the token from step 3
3. Deploy, open the URL, and sign in.

Every push to the repository redeploys the page. The news itself updates without any redeploy.

## Keeping it private

Use it for personal study only. Don't share the login or put the link anywhere public.
