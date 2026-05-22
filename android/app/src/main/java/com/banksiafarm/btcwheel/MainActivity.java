package com.banksiafarm.btcwheel;

import android.app.Activity;
import android.content.Intent;
import android.net.Uri;
import android.os.Bundle;

/**
 * Launcher activity.
 * Opens the bot farm web UI directly in the browser and finishes immediately
 * so the app icon acts like a browser shortcut — no setup screen shown.
 */
public class MainActivity extends Activity {

    private static final String BOT_URL = "https://bot.banksiaspringsfarm.com";

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        Intent intent = new Intent(Intent.ACTION_VIEW, Uri.parse(BOT_URL));
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        startActivity(intent);
        finish();
    }
}
