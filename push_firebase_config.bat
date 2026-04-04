@echo off
cd /d "C:\Users\alexj\OneDrive\Documents\Claude\Projects\MLB Daily Results"

echo Adding firebase.json and .firebaserc to repo...
git add firebase.json .firebaserc
git commit -m "Add Firebase config files"
git push origin main

echo Done!
pause
