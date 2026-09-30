@echo off
rem Convenience wrapper. The real installer lives at the repository root so it can
rem find both this plugin folder and the top-level project layout.
call "%~dp0..\..\..\install-orca-support-fins.bat" %*
