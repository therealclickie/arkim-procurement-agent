import glob, os
for p in glob.glob("**/known_parts*", recursive=True) + glob.glob("**/*known_parts*", recursive=True):
    print(p, os.path.getsize(p), "bytes")
