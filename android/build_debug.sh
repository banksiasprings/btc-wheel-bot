#!/bin/bash
export JAVA_HOME="/Applications/Android Studio.app/Contents/jbr/Contents/Home"
export ANDROID_HOME="$HOME/Library/Android/sdk"
cd "$HOME/Documents/btc-wheel-bot/android"
./gradlew assembleDebug --no-daemon 2>&1
