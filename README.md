Abella for Sublime Text 3
====
Support for the [Abella](https://abella-prover.org/) theorem prover in Sublime Text 3.

The develop of this plugin is mainly based on a [Coq plugin](https://github.com/whitequark/Sublime-Coq) for ST.

Installation
===
The recommended way to install Sublime Abella is to use Package Control.

It is also possible to install it using git. Navigate to the Sublime Text Packages folder, then run:

git clone git@github.com:JimmyZJX/SublimeAbella.git Abella

Usage
===
You need to open an Abella script file (*.thm) in ST3, and the following default key bindings should work:
- Ctrl+Enter: start Abella if necessary, and navigate to cursor.
- Ctrl+Down: navigate to the next statement.
- Ctrl+Up: undo last statement
- Ctrl+Right: navigate to cursor (basically equivalent to Ctrl+Enter)
- Ctrl+Left: reload Abella, and go to cursor
- Ctrl+Shift+Enter: kill Abella and reset the state
- Ctrl+';': show Theorem

And there are also shortcuts that helps writing proofs:
- Ctrl+7: (7 stands for "&", and) insert text " /\\ "
- Ctrl+'\\': ('\\' stands for "|", or) insert text " \\/ "
- Ctrl+'.': ('.' stands for ">", arrow) insert text " -> "

Executable
===
The `abella` executable should be in PATH by default.

If you want to change the location, edit your user setting `abella.exec` and point to the currect **file**. The default value is `abella`, and you may change it to `\path\to\abella`.

Highlighting
====

In order to get nice background highlighting for the proven parts of the file, add the following snippet to your color scheme file.

For `tmTheme` syntax:

<details><summary>dark themes</summary><p>

```xml
<dict>
  <key>name</key>
  <string>Proven with Abella</string>
  <key>scope</key>
  <string>meta.abella.proven</string>
  <key>settings</key>
  <dict>
    <key>background</key>
    <string>#365A28</string>
    <key>foreground</key>
    <string>#51873C</string>
  </dict>
</dict>
```
</p></details>

<details><summary>light themes</summary><p>

```xml
<dict>
  <key>name</key>
  <string>Proven with Abella</string>
  <key>scope</key>
  <string>meta.abella.proven</string>
  <key>settings</key>
  <dict>
    <key>background</key>
    <string>#002800</string>
  </dict>
</dict>
```
</p></details>

For `sublime-color-scheme` syntax:

<details><summary>dark themes</summary><p>

```json
{
    "name": "Proven with Abella",
    "scope": "meta.abella.proven",
    "background": "#365A28",
    "foreground": "#51873C"
},
```
</p></details>

<details><summary>light themes</summary><p>

```json
{
    "name": "Proven with Abella",
    "scope": "meta.abella.proven",
    "background": "#002800",
},
```
</p></details>
