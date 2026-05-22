package com.banksiafarm.btcwheel;

import android.app.Activity;
import android.os.Bundle;
import android.widget.TextView;
import android.graphics.Color;
import android.view.Gravity;
import android.widget.LinearLayout;

/**
 * Minimal launcher activity.
 * The real UI is the home-screen widget (BotFarmWidget).
 * This activity just shows a short info message.
 */
public class MainActivity extends Activity {

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setGravity(Gravity.CENTER);
        root.setBackgroundColor(Color.parseColor("#1a1a2e"));

        TextView tv = new TextView(this);
        tv.setText("⚡ BTC Bot Farm\n\nLong-press your home screen\nand add the Bot Farm widget.");
        tv.setTextColor(Color.WHITE);
        tv.setTextSize(18f);
        tv.setGravity(Gravity.CENTER);
        tv.setPadding(48, 48, 48, 48);

        root.addView(tv);
        setContentView(root);
    }
}
