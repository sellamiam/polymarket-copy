# Hosting your Polymarket Simulation Bot on Render.com (100% Free)

Since your Git repository is located at `/Users/mohamedsellamia/Scripts` and the simulation code is in the `/Polymarlet` subdirectory, follow this guide to set it up on Render for free.

---

## Step 1: Push your latest local changes to GitHub
Make sure the new `requirements.txt` file and your code are pushed to your GitHub repository:
1. Open your terminal in `/Users/mohamedsellamia/Scripts`.
2. Stage and commit the files:
   ```bash
   git add Polymarlet/
   git commit -m "Configure Polymarlet for Render deployment"
   git push origin main
   ```

---

## Step 2: Create a Web Service on Render
1. Log in to [Render.com](https://render.com) (you can log in using GitHub).
2. Click the **New +** button in the top right and select **Web Service**.
3. Connect your GitHub repository.

---

## Step 3: Configure the Web Service Settings
Fill out the creation form with these settings:

- **Name**: `polycopy-sim` (or any name you prefer)
- **Language**: `Python`
- **Root Directory**: `Polymarlet` *(Critical: This tells Render that your code is inside the subfolder)*
- **Branch**: `main`
- **Build Command**: `pip install -r requirements.txt`
- **Start Command**: `uvicorn main:app --host 0.0.0.0 --port $PORT`
- **Instance Type**: `Free`

Click **Create Web Service** at the bottom of the page. Render will automatically build the server and deploy it.

---

## Step 4: Keep the Server Awake 24/7 (Preventing Sleep Mode)
> [!IMPORTANT]
> Render's **Free Tier** automatically puts Web Services to sleep after **15 minutes of inactivity** (no incoming web traffic). If the server sleeps, the python background poller thread will temporarily pause until a request wakes it up.
> 
To bypass this limitation and keep the bot running 24/7 without paying a dime:
1. Go to [UptimeRobot.com](https://uptimerobot.com) (completely free) and create an account.
2. Click **Add New Monitor**.
3. Set **Monitor Type** to `HTTP(s)`.
4. Set **Friendly Name** to `PolyCopy KeepAlive`.
5. Set the **URL (or IP)** to your Render URL with the API endpoint, for example:
   `https://polycopy-sim.onrender.com/api/state`
6. Set **Monitoring Interval** to `Every 5 minutes` (or `Every 10 minutes`).
7. Click **Create Monitor**.

UptimeRobot will now ping your bot's state API endpoint every few minutes. This keeps the server constantly active and awake, allowing your simulation bot to poll the Polymarket whales around the clock!
