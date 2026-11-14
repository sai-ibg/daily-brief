// Vercel function behind the page's Refresh button.
// Checks the caller is a signed-in reader, then starts the GitHub "Collect news" workflow.
//
// Vercel environment variables:
//   SUPABASE_URL       https://<project>.supabase.co
//   SUPABASE_ANON_KEY  the project's publishable (anon) key
//   GH_REPO            owner/repo
//   GH_TOKEN           fine-grained token with "Actions: read and write" on that repo only
//   GH_BRANCH          optional, defaults to main

module.exports = async (req, res) => {
  if (req.method !== "POST") return res.status(405).send("POST only");

  const { SUPABASE_URL, SUPABASE_ANON_KEY, GH_REPO, GH_TOKEN } = process.env;
  if (!SUPABASE_URL || !SUPABASE_ANON_KEY || !GH_REPO || !GH_TOKEN) {
    return res.status(500).send("Refresh is not set up on the server yet");
  }

  const auth = req.headers.authorization || "";
  if (!auth.startsWith("Bearer ")) return res.status(401).send("Sign in first");

  // Only readers can see the runs row, so reading it is the permission check.
  const check = await fetch(`${SUPABASE_URL.replace(/\/$/, "")}/rest/v1/dailybrief_runs?select=id&id=eq.1`, {
    headers: { apikey: SUPABASE_ANON_KEY, Authorization: auth },
  });
  const rows = check.ok ? await check.json() : [];
  if (!rows.length) return res.status(403).send("Not allowed");

  const gh = await fetch(`https://api.github.com/repos/${GH_REPO}/actions/workflows/scrape.yml/dispatches`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${GH_TOKEN}`,
      Accept: "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "daily-brief",
    },
    body: JSON.stringify({ ref: process.env.GH_BRANCH || "main" }),
  });
  if (gh.status !== 204) return res.status(502).send(`GitHub refused the run (${gh.status})`);

  return res.status(202).json({ started: true });
};
