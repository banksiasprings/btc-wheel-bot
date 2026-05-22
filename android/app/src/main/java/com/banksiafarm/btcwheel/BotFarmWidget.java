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
import java.text.SimpleDateFormat;
import java.util.Date;
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
 */
public class BotFarmWidget extends AppWidgetProvider {
    private static final String TAG = "BotFarmWidget";
    private static final String ACTION_REFRESH = "com.banksiafarm.btcwheel.WIDGET_REFRESH";
    private static final String PREFS_NAME = "BotFarmWidgetPrefs";

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
                              boolean offline) {
        RemoteViews rv = new RemoteViews(ctx.getPackageName(), R.layout.widget_bot_farm);

        // Tap-to-open-dashboard PendingIntent
        Intent launchIntent = new Intent(Intent.ACTION_VIEW,
            Uri.parse(BASE_URL + "/widget"));
        launchIntent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        PendingIntent launchPi = PendingIntent.getActivity(ctx, 0, launchIntent,
            PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        rv.setOnClickPendingIntent(R.id.widget_root, launchPi);

        // Status dot + label
        if (offline) {
            rv.setTextColor(R.id.tv_status_dot, Color.parseColor("#ef4444"));
            rv.setTextViewText(R.id.tv_status_dot, "●");
            rv.setTextViewText(R.id.tv_status_label, "OFFLINE");
            rv.setTextColor(R.id.tv_status_label, Color.parseColor("#ef4444"));
        } else if (data != null && data.farmRunning) {
            rv.setTextColor(R.id.tv_status_dot, Color.parseColor("#22c55e"));
            rv.setTextViewText(R.id.tv_status_dot, "●");
            rv.setTextViewText(R.id.tv_status_label, "RUNNING");
            rv.setTextColor(R.id.tv_status_label, Color.parseColor("#22c55e"));
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

        // Footer timestamp
        String timestamp = new SimpleDateFormat("h:mm a", Locale.US).format(new Date());
        rv.setTextViewText(R.id.tv_updated,
            (offline ? "⚠ Offline — last: " : "Updated ") + timestamp);

        mgr.updateAppWidget(widgetId, rv);
    }

    // ── HTTP fetch — uses /farm/status and /farm/equity ────────────────────
    private FetchResult fetchBotData() {
        FetchResult result = new FetchResult();
        try {
            // 1. Fetch /farm/status
            JSONObject farmStatus = getJson(BASE_URL + "/farm/status");
            if (farmStatus == null) return null;

            result.farmRunning = farmStatus.optBoolean("farm_running", false);

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
    }
}
