# Am Israel Hai

![Time in Shelter](badges/shelter.svg)

*Data from [tzevaadom.co.il](https://tzevaadom.co.il/) · Updated every 30 minutes*

---

## Use this badge in your own repo

This badge tracks time spent in a bomb shelter based on Home Front Command alerts for your location. It updates automatically every 30 minutes via GitHub Actions.

### 1. Fork this repo

Click **Fork** at the top right.

### 2. Set your area

Open `config.toml` in your fork and set your city name:

```toml
[area]
names = ["your city here"]
```

Find your exact city name in [`areas.txt`](areas.txt) — open the file and Ctrl+F for your city.
You can list multiple names if your area has old and new variants.

> `[github].username` is optional — it's auto-detected in GitHub Actions from your repo name.

### 3. Allow Actions to write to the repo

In your fork: **Settings → Actions → General**
- Under "Actions permissions": select **Allow all actions and reusable workflows**
- Under "Workflow permissions": select **Read and write permissions**

### 4. Run the first update

Go to **Actions → Update Shelter Badge → Run workflow**.
The first run takes ~20 minutes (backfills 30 days of history).

### 5. Embed the badge

In the **same repo's README**:
```markdown
![Time in Shelter](badges/shelter.svg)
```

To embed **anywhere else** (another repo, website, etc.):
```markdown
![Time in Shelter](https://raw.githubusercontent.com/YOUR_USERNAME/am-israel-hai-badge/master/badges/shelter.svg)
```

---

### Changed your city?

Go to **Actions → Update Shelter Badge → Run workflow**, enable the **resync** checkbox.
This clears the cached data and re-backfills for the new area.
