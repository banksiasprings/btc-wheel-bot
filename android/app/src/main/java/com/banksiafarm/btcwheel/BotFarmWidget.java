package com.banksiafarm.btcwheel;

import android.app.PendingIntent;
import android.appwidget.AppWidgetManager;
import android.appwidget.AppWidgetProvider;
import android.content.Context;
import android.content.Intent;
import android.net.Uri;
import android.content.SharedPreferences;
import android.graphics.Color;
import android.widget.RemoteViews;
import android.util.Log;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.net.HttpURLConnection;
import java.net.URL;
import java.util.Locale;

/**
 * BTC Wheel Bot — Android home screen widget.
 *
 * Polls two farm endpoints (both require X-Api-Key header):
 *   GET /farm/status  → farm_running, bot counts, positions
 *   GET /farm/equity  → total_current, total_starting, total_return_pct
 *
 * The widget refreshes every 30 minutes via the AppWidgetProvider system.
 * Tapping the widget triggers an immediate manual refresh.
 *
 * Freshness model (3-state) — the farm only ticks HOURLY, so a tight "is the
 * last poll fresh?" check would read red for most of every hour. Status is
 * instead derived from the age of the farm's last tick (the `updated` field
 * from /farm/status), NOT from whether a single poll happened to succeed:
 *   ✅ ONLINE  — poll succeeded AND last tick ≤ 75 min ago (within the hourly window + slack)
 *   ⏳ STALE   — poll blipped but cached data is recent, OR last tick 75–180 min ago (a tick skipped)
 *   🔴 OFFLINE — never fetched, OR last tick > 180 min ago (~3h: supervisor down / phone offline too long)
 * A single transient fetch failure no longer alarms — we keep showing the last
 * known data with "Updated Xm ago" until it genuinely goes stale.
 */
public class BotFarmWidget extends AppWidgetProvider {
    private static final String TAG = "BotFarmWidget";
    private static final String ACTION_REFRESH = "com.banksiafarm.btcwheel.WIDGET_REFRESH";
    private static final String PREFS_NAME = "BotFarmWidgetPrefs";

    // ── Freshness thresholds (minutes) — farm ticks hourly; see CONTEXT.md ──
    /** ≤ this since the last tick = within the current hourly window (+15m slack) → ONLINE. */
    private static final long FRESH_MAX_MIN = 75;
    /** ≤ this = a tick or two skipped, still plausibly fine → STALE (amber). Beyond → OFFLINE (red). */
    private static final long STALE_MAX_MIN = 180;

    // Status states
    private static final int ST_ONLINE  = 0;
    private static final int ST_STALE   = 1;
    private static final int ST_OFFLINE = 2;

    // ── API config ──────────────────────────────────────────────────────────
    private static final String BASE_URL = BuildConfig.BOT_API_URL;
    private static final String API_KEY  = BuildConfig.BOT_API_KEY;

    // ── Cache keys (SharedPreferences) ─────────────────────────────────────
    private static final String KEY_EQUITY        = "last_equity";
    private static final String KEY_START_EQUITY  = "last_start_equity";
    private static final String KEY_RETURN_PCT    = "last_return_pct";
    private static final String KEY_RUNNING_COUNT = "last_running_count";
    private static final String KEY_TOTAL_BOTS    = "last_total_bots";
    private static final String KEY_OPEN_POS      = "last_open_positions";
    private static final String KEY_FARM_RUNNING  = "last_farm_running";
    private static final String KEY_UPDATED_MS    = "last_updated_ms";   // when WE last fetched OK
    private static final String KEY_TICK_MS       = "last_tick_ms";      // server `updated` = last farm tick

    // ── AppWidgetProvider callbacks ─────────────────────────────────────────
    @Override
    public void onUpdate(Context context, AppWidgetManager appWidgetManager, int[] appWidgetIds) {
        for (int appWidgetId : appWidgetIds) {
            triggerFetch(context, appWidgetManager, appWidgetId);
        }
    }

    @Override
    public void onReceive(Context context, Intent intent) {
        super.onReceive(context, intent);
        if (ACTION_REFRESH.equals(intent.getAction())) {
            AppWidgetManager manager = AppWidgetManager.getInstance(context);
            int[] ids = manager.getAppWidgetIds(
                new android.content.ComponentName(context, BotFarmWidget.class));
            for (int id : ids) {
                triggerFetch(context, manager, id);
            }
        }
    }

    // ── Fetch + render (runs background thread per widget) ─────────────────
    private void triggerFetch(final Context ctx,
                               final AppWidgetManager mgr,
                               final int widgetId) {
        showLoading(ctx, mgr, widgetId);
        new Thread(() -> {
            FetchResult result = fetchBotData();
            if (result != null) {
                saveToPrefs(ctx, result);
                updateViews(ctx, mgr, widgetId, result, false);
            } else {
                FetchResult cached = loadFromPrefs(ctx);
                updateViews(ctx, mgr, widgetId, cached, true);
            }
        }).start();
    }

    // ── Loading placeholder ─────────────────────────────────────────────────
    private void showLoading(Context ctx, AppWidgetManager mgr, int widgetId) {
        RemoteViews rv = new RemoteViews(ctx.getPackageName(), R.layout.widget_bot_farm);
        rv.setTextViewText(R.id.tv_updated, "Refreshing…");
        mgr.partiallyUpdateAppWidget(widgetId, rv);
    }

    // ── Build + push RemoteViews ────────────────────────────────────────────
    private void updateViews(Context ctx,
                              AppWidgetManager mgr,
                              int widgetId,
                              FetchResult data,
                              boolean fetchFailed) {
        RemoteViews rv = new RemoteViews(ctx.getPackageName(), R.layout.widget_bot_farm);

        // Tap-to-open-dashboard PendingIntent
        Intent launchIntent = new Intent(Intent.ACTION_VIEW,
            Uri.parse(BASE_URL + "/widget"));
        launchIntent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        PendingIntent launchPi = PendingIntent.getActivity(ctx, 0, launchIntent,
            PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        rv.setOnClickPendingIntent(R.id.widget_root, launchPi);

        // ── Derive 3-state freshness from the age of the last farm tick ──
        // We trust the server `updated` time (last tick), not whether this one
        // poll succeeded — so a transient blip won't flip the widget to red.
        boolean haveTick = data != null && data.tickMs > 0;
        long tickAgeMin = haveTick
            ? (System.currentTimeMillis() - data.tickMs) / 60_000L
            : Long.MAX_VALUE;

        int state;
        if (!haveTick || tickAgeMin > STALE_MAX_MIN) {
            state = ST_OFFLINE;                       // never fetched, or tick > ~3h old → real outage
        } else if (fetchFailed || tickAgeMin > FRESH_MAX_MIN) {
            state = ST_STALE;                         // transient blip on recent data, or a tick skipped
        } else {
            state = ST_ONLINE;                        // fresh poll + tick within the hourly window
        }

        // Status dot (emoji) + label, coloured to match
        if (state == ST_ONLINE) {
            rv.setTextViewText(R.id.tv_status_dot, "✅");
            rv.setTextViewText(R.id.tv_status_label, "ONLINE");
            rv.setTextColor(R.id.tv_status_label, Color.parseColor("#22c55e"));
        } else if (state == ST_STALE) {
            rv.setTextViewText(R.id.tv_status_dot, "⏳");
            rv.setTextViewText(R.id.tv_status_label, "STALE");
            rv.setTextColor(R.id.tv_status_label, Color.parseColor("#f59e0b"));
        } else {
            rv.setTextViewText(R.id.tv_status_dot, "🔴");
            rv.setTextViewText(R.id.tv_status_label, "OFFLINE");
            rv.setTextColor(R.id.tv_status_label, Color.parseColor("#ef4444"));
        }

        // Equity
        if (data != null && data.currentEquity > 0) {
            rv.setTextViewText(R.id.tv_equity, formatDollars(data.currentEquity));
        } else {
            rv.setTextViewText(R.id.tv_equity, "$—");
        }

        // P&L line — plain English "profit/loss"
        if (data != null && data.currentEquity > 0 && data.startEquity > 0) {
            double pnl = data.currentEquity - data.startEquity;
            String sign = pnl >= 0 ? "+" : "";
            String pnlText = sign + formatDollars(pnl) + " (" + sign
                + String.format(Locale.US, "%.1f", data.returnPct) + "%)";
            rv.setTextViewText(R.id.tv_pnl, pnlText);
            rv.setTextColor(R.id.tv_pnl,
                Color.parseColor(pnl >= 0 ? "#22c55e" : "#ef4444"));
        } else {
            rv.setTextViewText(R.id.tv_pnl, "— (—%)");
            rv.setTextColor(R.id.tv_pnl, Color.parseColor("#888888"));
        }

        // "Bots active" count — plain English
        if (data != null && data.totalBots > 0) {
            rv.setTextViewText(R.id.tv_mode,
                data.runningCount + "/" + data.totalBots + " bots");
        } else {
            rv.setTextViewText(R.id.tv_mode, "—");
        }

        // Open positions count
        if (data != null) {
            rv.setTextViewText(R.id.tv_uptime,
                data.openPositions + " open position" + (data.openPositions != 1 ? "s" : ""));
        } else {
            rv.setTextViewText(R.id.tv_uptime, "—");
        }

        // Return %
        if (data != null && data.returnPct != Double.MIN_VALUE) {
            String ret = String.format(Locale.US, "%+.2f%%", data.returnPct);
            rv.setTextViewText(R.id.tv_return, ret);
            rv.setTextColor(R.id.tv_return,
                Color.parseColor(data.returnPct >= 0 ? "#22c55e" : "#ef4444"));
        } else {
            rv.setTextViewText(R.id.tv_return, "—%");
            rv.setTextColor(R.id.tv_return, Color.parseColor("#888888"));
        }

        // Footer: data freshness, expressed as the age of the last farm tick.
        // (Relative age, not wall-clock — Steven cares "how old is this data".)
        String footer;
        if (!haveTick) {
            footer = "Waiting for first data…";
        } else if (state == ST_OFFLINE) {
            footer = "⚠ No update for " + formatAge(tickAgeMin);
        } else if (fetchFailed) {
            footer = "Updated " + formatAge(tickAgeMin) + " · reconnecting…";
        } else {
            footer = "Updated " + formatAge(tickAgeMin);
        }
        rv.setTextViewText(R.id.tv_updated, footer);

        mgr.updateAppWidget(widgetId, rv);
    }

    /** Human-friendly relative age, e.g. "just now", "7m ago", "1h 5m ago". */
    private String formatAge(long ageMin) {
        if (ageMin < 1)  return "just now";
        if (ageMin < 60) return ageMin + "m ago";
        long h = ageMin / 60, m = ageMin % 60;
        return m == 0 ? h + "h ago" : h + "h " + m + "m ago";
    }

    // ── HTTP fetch — uses /farm/status and /farm/equity ────────────────────
    private FetchResult fetchBotData() {
        FetchResult result = new FetchResult();
        try {
            // 1. Fetch /farm/status
            JSONObject farmStatus = getJson(BASE_URL + "/farm/status");
            if (farmStatus == null) return null;

            result.farmRunning = farmStatus.optBoolean("farm_running", false);
            result.tickMs      = parseTickMs(farmStatus.optString("updated", ""));

            JSONArray bots = farmStatus.optJSONArray("bots");
            if (bots != null) {
                result.totalBots = bots.length();
                for (int i = 0; i < bots.length(); i++) {
                    JSONObject bot = bots.getJSONObject(i);
                    if ("running".equals(bot.optString("status"))) {
                        result.runningCount++;
                    }
                    if (bot.optBoolean("has_open_position", false)) {
                        result.openPositions++;
                    }
                }
            }

            // 2. Fetch /farm/equity
            JSONObject equity = getJson(BASE_URL + "/farm/equity");
            if (equity != null) {
                result.currentEquity = equity.optDouble("total_current", 0);
                result.startEquity   = equity.optDouble("total_starting", 0);
                result.returnPct     = equity.optDouble("total_return_pct", Double.MIN_VALUE);
            }

            return result;
        } catch (Exception e) {
            Log.e(TAG, "fetchBotData failed: " + e.getMessage());
            return null;
        }
    }

    /**
     * Parse the farm's `updated` timestamp (ISO-8601 UTC, e.g.
     * "2026-05-29T07:00:00.123456+00:00") to epoch millis. 0 if absent/unparseable.
     * minSdk is 26, so java.time is available.
     */
    private long parseTickMs(String iso) {
        if (iso == null || iso.isEmpty()) return 0L;
        try {
            return java.time.OffsetDateTime.parse(iso).toInstant().toEpochMilli();
        } catch (Exception e) {
            try {
                return java.time.Instant.parse(iso).toEpochMilli();   // fallback for a 'Z' suffix
            } catch (Exception ignored) {
                return 0L;
            }
        }
    }

    /** GET a URL with the API key header; returns parsed JSONObject or null. */
    private JSONObject getJson(String urlStr) {
        HttpURLConnection conn = null;
        try {
            URL url = new URL(urlStr);
            conn = (HttpURLConnection) url.openConnection();
            conn.setRequestMethod("GET");
            conn.setConnectTimeout(8_000);
            conn.setReadTimeout(8_000);
            if (API_KEY != null && !API_KEY.isEmpty()) {
                conn.setRequestProperty("X-API-Key", API_KEY);
            }
            int code = conn.getResponseCode();
            if (code != 200) {
                Log.w(TAG, "HTTP " + code + " from " + urlStr);
                return null;
            }
            BufferedReader br = new BufferedReader(
                new InputStreamReader(conn.getInputStream(), "UTF-8"));
            StringBuilder sb = new StringBuilder();
            String line;
            while ((line = br.readLine()) != null) sb.append(line);
            br.close();
            return new JSONObject(sb.toString());
        } catch (Exception e) {
            Log.e(TAG, "getJson(" + urlStr + "): " + e.getMessage());
            return null;
        } finally {
            if (conn != null) conn.disconnect();
        }
    }

    // ── SharedPreferences cache ─────────────────────────────────────────────
    private void saveToPrefs(Context ctx, FetchResult r) {
        ctx.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
            .edit()
            .putLong(KEY_UPDATED_MS, System.currentTimeMillis())
            .putLong(KEY_TICK_MS, r.tickMs)
            .putFloat(KEY_EQUITY, (float) r.currentEquity)
            .putFloat(KEY_START_EQUITY, (float) r.startEquity)
            .putFloat(KEY_RETURN_PCT, (float) r.returnPct)
            .putBoolean(KEY_FARM_RUNNING, r.farmRunning)
            .putInt(KEY_RUNNING_COUNT, r.runningCount)
            .putInt(KEY_TOTAL_BOTS, r.totalBots)
            .putInt(KEY_OPEN_POS, r.openPositions)
            .apply();
    }

    private FetchResult loadFromPrefs(Context ctx) {
        SharedPreferences p = ctx.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE);
        FetchResult r = new FetchResult();
        r.currentEquity  = p.getFloat(KEY_EQUITY, 0f);
        r.startEquity    = p.getFloat(KEY_START_EQUITY, 0f);
        r.returnPct      = p.getFloat(KEY_RETURN_PCT, (float) Double.MIN_VALUE);
        r.farmRunning    = p.getBoolean(KEY_FARM_RUNNING, false);
        r.tickMs         = p.getLong(KEY_TICK_MS, 0L);
        r.runningCount   = p.getInt(KEY_RUNNING_COUNT, 0);
        r.totalBots      = p.getInt(KEY_TOTAL_BOTS, 0);
        r.openPositions  = p.getInt(KEY_OPEN_POS, 0);
        return r;
    }

    // ── Formatting helpers ──────────────────────────────────────────────────
    private String formatDollars(double value) {
        if (Math.abs(value) >= 1_000_000)
            return String.format(Locale.US, "$%.2fM", value / 1_000_000);
        if (Math.abs(value) >= 1_000)
            return String.format(Locale.US, "$%,.0f", value);
        return String.format(Locale.US, "$%.2f", value);
    }

    // ── Data holder ─────────────────────────────────────────────────────────
    static class FetchResult {
        boolean farmRunning   = false;
        int     runningCount  = 0;
        int     totalBots     = 0;
        int     openPositions = 0;
        double  currentEquity = 0;
        double  startEquity   = 0;
        double  returnPct     = Double.MIN_VALUE;
        long    tickMs        = 0L;     // epoch ms of the farm's last tick (server `updated`)
    }
}
