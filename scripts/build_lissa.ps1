$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$lissaRoot = Join-Path $projectRoot 'vendor/lissa/LiSSA-RATLR-V2/lissa'
$mavenCache = Join-Path $projectRoot 'artifacts/lissa/m2'
$bridgeOutput = Join-Path $projectRoot 'artifacts/lissa/bridge'
$jarPath = Join-Path $lissaRoot 'target/ratlr-0.2.0-SNAPSHOT-jar-with-dependencies.jar'
python (Join-Path $PSScriptRoot 'fetch_lissa.py')
if ($LASTEXITCODE -ne 0) { throw 'LiSSA source verification failed' }
New-Item -ItemType Directory -Force $mavenCache, $bridgeOutput | Out-Null
mvn -B -f (Join-Path $lissaRoot 'pom.xml') "-Dmaven.repo.local=$mavenCache" -DskipTests package
if ($LASTEXITCODE -ne 0) { throw 'LiSSA Maven build failed' }
javac -encoding UTF-8 -cp $jarPath -d $bridgeOutput (Join-Path $projectRoot 'integrations/lissa/LissaRetrievalBridge.java')
if ($LASTEXITCODE -ne 0) { throw 'LiSSA bridge compilation failed' }
Write-Output "LiSSA JAR: $jarPath"
Write-Output "Bridge: $bridgeOutput"
