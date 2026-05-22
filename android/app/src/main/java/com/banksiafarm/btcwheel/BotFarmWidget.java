package com.banksiafarm.btcwheel;

import android.app.PendingIntent;
import android.appwidget.AppWidgetManager;
import android.appwidget.AppWidgetProvider;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.graphics.Color;
import android.widget.RemoteViews;
import android.util.Log;

import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.net.HttpURLConnection;
import java.net.URL;
import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.Locale;

/**
 * BTC Wheel Bot — Android home screen widget.
 *
 * Polls two endpoints (both require X-Api-Key header):
 *   GET /status   → bot_running, paused, mode, uptime_seconds, last_heartbeat
 *   GET /equity   → current_equity, starting_equity, total_return_pct
 *
 * The widget refreshes every 30 minutes via the AppWidgetProvider system.
 * Tapping the widget triggers an immediate manual refresh.
 */
public class BotFarmWidget extends AppWidgetProvider {

    private static final String TAG = "BotFarmWidget";
    private static final String ACTION_REFRESH = "com.banksiafarm.btcwheel.WIDGET_REFRESH";
    private static final String PREFS_NAME = "BotFarmWidgetPrefs";

    // ── API config ──────────────────────────────────────────────────────────
    // Base URL for the bot farm API. Change to your server address if needed.
    private static final String BASE_URL = BuildConfig.BOT_API_URL;
    private static final String API_KEY  = BuildConfig.BOT_API_KEY;

    // ── Cache keys (SharedPreferences) ─────────────────────────────────────
    private static final String KEY_EQUITY        = "last_equity";
    private static final String KEY_START_EQUITY  = "last_start_equity";
    private static final String KEY_RETURN_PCT    = "last_return_pct";
    private static final String KEY_MODE          = "last_mode";
    private static final String KEY_UPTIME        = "last_uptime";
    private static final String KEY_RUNNING       = "last_running";
    private static final String KEY_UPDATED_MS    = "last_updated_ms";

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
        // Show "refreshing…" state immediately on the UI thread
        showLoading(ctx, mgr, widgetId);

        new Thread(() -> {
            FetchResult result = fetchBotData();
            if (result != null) {
                // Persist to prefs so we can show last-known on failures
                saveToPrefs(ctx, result);
                updateViews(ctx, mgr, widgetId, result, false);
            } else {
                // Fetch failed — show cached data with "Offline" badge
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
                              boolean offline) {

        RemoteViews rv = new RemoteViews(ctx.getPackageName(), R.layout.widget_bot_farm);

        // Tap-to-refresh PendingIntent
        Intent refreshIntent = new Intent(ctx, BotFarmWidget.class);
        refreshIntent.setAction(ACTION_REFRESH);
        PendingIntent pi = PendingIntent.getBroadcast(
            ctx, 0, refreshIntent,
            PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        rv.setOnClickPendingIntent(R.id.widget_root, pi);

        // Status dot + label
        if (offline) {
            rv.setTextColor(R.id.tv_status_dot, Color.parseColor("#ef4444"));
            rv.setTextViewText(R.id.tv_status_dot, "●");
            rv.setTextViewText(R.id.tv_status_label, "OFFLINE");
            rv.setTextColor(R.id.tv_status_label, Color.parseColor("#ef4444"));
        } else if (data != null && data.botRunning) {
            rv.setTextColor(R.id.tv_status_dot, Color.parseColor(data.paused ? "#f59e0b" : "#22c55e"));
            rv.setTextViewText(R.id.tv_status_dot, "●");
            rv.setTextViewText(R.id.tv_status_label, data.paused ? "PAUSED" : "LIVE");
            rv.setTextColor(R.id.tv_status_label, Color.parseColor(data.paused ? "#f59e0b" : "#22c55e"));
        } else {
            rv.setTextColor(R.id.tv_status_dot, Color.parseColor("#ef4444"));
            rv.setTextViewText(R.id.tv_status_dot, "●");
            rv.setTextViewText(R.id.tv_status_label, "STOPPED");
            rv.setTextColor(R.id.tv_status_label, Color.parseColor("#ef4444"));
        }

        // Equity
        if (data != null && data.currentEquity > 0) {
            rv.setTextViewText(R.id.tv_equity, formatDollars(data.currentEquity));
        } else {
            rv.setTextViewText(R.id.tv_equity, "$—");
        }

        // P&L line
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

        // Mode
        rv.setTextViewText(R.id.tv_mode,
            (data != null && data.mode != null && !data.mode.isEmpty())
                ? data.mode.toUpperCase(Locale.US) : "—");

        // Uptime
        rv.setTextViewText(R.id.tv_uptime,
            (data != null && data.uptimeSeconds >= 0)
                ? formatUptime(data.uptimeSeconds) : "—");

        // Return %
        if (data != null && data.returnPct != Double.MIN_VALUE) {
            String ret = String.format(Locale.US, "%+.1f%%", data.returnPct);
            rv.setTextViewText(R.id.tv_return, ret);
            rv.setTextColor(R.id.tv_return,
                Color.parseColor(data.returnPct >= 0 ? "#22c55e" : "#ef4444"));
        } else {
            rv.setTextViewText(R.id.tv_return, "—%");
            rv.setTextColor(R.id.tv_return, Color.parseColor("#888888"));
        }

        // Footer
        String timestamp = new SimpleDateFormat("h:mm a", Locale.US).format(new Date());
        rv.setTextViewText(R.id.tv_updated,
            (offline ? "⚠ Offline — last: " : "Updated ") + timestamp);

        mgr.updateAppWidget(widgetId, rv);
    }

    // ── HTTP fetch ──────────────────────────────────────────────────────────

    private FetchResult fetchBotData() {
        FetchResult result = new FetchResult();
        try {
            // 1. Fetch /status
            JSONObject status = getJson(BASE_URL + "/status");
            if (status == null) return null;
            result.botRunning = status.optBoolean("bot_running", false);
            result.paused     = status.optBoolean("paused", false);
            result.mode       = status.optString("mode", "unknown");
            result.uptimeSeconds = status.optLong("uptime_seconds", -1L);

            // 2. Fetch /equity
            JSONObject equity = getJson(BASE_URL + "/equity");
            if (equity != null) {
                result.currentEquity = equity.optDouble("current_equity", 0);
                result.startEquity   = equity.optDouble("starting_equity", 0);
                result.returnPct     = equity.optDouble("total_return_pct", Double.MIN_VALUE);
            }
            return result;

        } catch (Exception e) {
            Log.e(TAG, "fetchBotData failed: " + e.getMessage());
            return null;
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
                conn.setRequestProperty("X-Api-Key", API_KEY);
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
            .putFloat(KEY_EQUITY, (float) r.currentEquity)
            .putFloat(KEY_START_EQUITY, (float) r.startEquity)
            .putFloat(KEY_RETURN_PCT, (float) r.returnPct)
            .putString(KEY_MODE, r.mode)
            .putLong(KEY_UPTIME, r.uptimeSeconds)
            .putBoolean(KEY_RUNNING, r.botRunning)
            .apply();
    }

    private FetchResult loadFromPrefs(Context ctx) {
        SharedPreferences p = ctx.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE);
        FetchResult r = new FetchResult();
        r.currentEquity  = p.getFloat(KEY_EQUITY, 0f);
        r.startEquity    = p.getFloat(KEY_START_EQUITY, 0f);
        r.returnPct      = p.getFloat(KEY_RETURN_PCT, (float) Double.MIN_VALUE);
        r.mode           = p.getString(KEY_MODE, "—");
        r.uptimeSeconds  = p.getLong(KEY_UPTIME, -1L);
        r.botRunning     = p.getBoolean(KEY_RUNNING, false);
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

    private String formatUptime(long seconds) {
        if (seconds < 0) return "—";
        long h = seconds / 3600;
        long m = (seconds % 3600) / 60;
        if (h >= 24) return (h / 24) + "d " + (h % 24) + "h";
        return h + "h " + m + "m";
    }

    // ── Data holder ─────────────────────────────────────────────────────────

    static class FetchResult {
        boolean botRunning    = false;
        boolean paused        = false;
        String  mode          = "unknown";
        long    uptimeSeconds = -1L;
        double  currentEquity = 0;
        double  startEquity   = 0;
        double  returnPct     = Double.MIN_VALUE;
    }
}
