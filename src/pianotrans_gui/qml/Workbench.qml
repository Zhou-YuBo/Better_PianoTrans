import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Shapes

ApplicationWindow {
    id: root
    visible: true
    width: 1440
    height: 820
    minimumWidth: 1000
    minimumHeight: 630
    title: "Better PianoTrans"
    color: root.bg
    palette.window: root.paper
    palette.windowText: root.ink
    palette.base: root.fill
    palette.text: root.ink
    palette.button: root.paper
    palette.buttonText: root.ink
    palette.highlight: root.sel
    palette.highlightedText: root.ink
    palette.placeholderText: root.faint

    // ── 令牌（proto-workbench.html :root）───────────────────────────
    readonly property color bg: "#f2f2f2"
    readonly property color paper: "#ffffff"
    readonly property color shell: "#ebebeb"
    readonly property color well: "#e3e3e3"
    readonly property color rule: "#d2d2d2"
    readonly property color strongRule: "#a6a6a6"
    readonly property color ink: "#191919"
    readonly property color muted: "#6f6f6f"
    readonly property color faint: "#9a9a9a"
    readonly property color fill: "#fbfbfb"
    readonly property color sel: "#e9e9e9"
    readonly property color accent: "#1f1f1f"
    readonly property color okLed: "#8fbc8f"
    readonly property color okLedEdge: "#6d9a6d"
    readonly property color bad: "#8f2f2f"
    readonly property string mono: "Consolas"

    property var data: facade.projection
    property var player: facade.playback
    property string page: "select"
    property string libraryTab: "history"
    property string selectedKind: ""
    property string selectedId: ""
    property string statusText: ""
    property string toastActionText: ""
    property string toastFavoriteId: ""
    property string submissionId: ""
    property bool preparingSubmission: false
    property var submitted: []
    property var fileCheck: ({})
    property string unverifiedRequestId: ""
    property var duplicateRecords: []
    property string conflictError: ""
    property var inspectedResult: ({})
    property var playingEntry: ({})
    property var selectedHistoryIds: []
    property var selectedFavoriteIds: []
    property string historyStatusValue: "all"
    property string favoriteChip: "all"
    property var extraHistory: []
    property var extraFavorites: []
    property var extraHistoryCursor: null
    property var extraFavoritesCursor: null
    property int seenRevision: -1
    property string editingCollectionId: ""
    property int editingCollectionRevision: 0
    property var notes: ({})
    property bool pedalDown: false
    property string batchDirectory: ""
    property bool batchSource: true
    property int batchSize: 16
    property string outputPickerTarget: "task"
    onStatusTextChanged: if (statusText) statusTimer.restart()

    function rows() {
        if (page === "select") return data.work.items
        if (page === "running") return data.active
        return libraryTab === "history" ? data.history.items.concat(extraHistory) : data.favorites.items.concat(extraFavorites)
    }
    function currentKind() { return page === "select" ? "task" : page === "running" ? "active" : libraryTab === "history" ? "history" : "favorite" }
    function idOf(row) { return row.taskId || row.attempt_id || row.favoriteId || row.favorite_id }
    function pathOf(row) { return row.inputPath || row.input_path || "" }
    function nameOf(row) { return pathOf(row).split(/[\\/]/).pop() }
    function directoryOf(row) { return pathOf(row).replace(/[\\/][^\\/]*$/, "") }
    function extOf(row) { return nameOf(row).split(".").pop().toUpperCase() }
    function midiOf(row) { return row.midi_path || row.midiFileName ? (row.midi_path || directoryOf(row) + "\\" + row.midiFileName) : "" }
    function stamp(iso) {
        var text = String(iso || "")
        if (text.length < 16) return text
        return text.substring(5, 10) + " " + text.substring(11, 16)
    }
    function recordTime(row) { return stamp(row.finished_at || row.queued_at) }
    function statusLabel(row) {
        if (row.status === "succeeded") return "已完成"
        if (row.status === "failed") return "失败"
        if (row.status === "cancelled") return "已取消"
        if (row.status === "running") return "转录中"
        if (row.status === "queued") return "等待中"
        return "已就绪"
    }
    function ledKind(row) {
        if (row.status === "succeeded") return "ready"
        if (row.status === "failed") return "fail"
        if (row.status === "cancelled") return "cancel"
        if (row.status === "running") return "run"
        return ""
    }
    function selectedRow() {
        if (selectedKind !== currentKind()) return null
        var list = rows()
        for (var i = 0; i < list.length; i++) if (idOf(list[i]) === selectedId) return list[i]
        return null
    }
    function ensureSelection() {
        if (selectedRow()) return
        var list = rows()
        if (list.length) select(currentKind(), idOf(list[0]))
        else select("", "")
    }
    function pageCheckedIds() {
        return data.work.items.filter(function(row) { return row.checked }).map(function(row) { return row.taskId })
    }
    function visibleWorkPages() {
        var current = data.work.page, count = data.work.pageCount
        var candidates = [1, current - 1, current, current + 1, count]
        return candidates.filter(function(value, index) {
            return value >= 1 && value <= count && candidates.indexOf(value) === index
        }).sort(function(a, b) { return a - b })
    }
    function shortTime(seconds) {
        var value = Math.max(0, Math.floor(seconds || 0))
        return Math.floor(value / 60).toString().padStart(2, "0") + ":" + (value % 60).toString().padStart(2, "0")
    }
    function select(kind, id) { selectedKind = kind; selectedId = id }
    onPageChanged: Qt.callLater(ensureSelection)
    onLibraryTabChanged: Qt.callLater(ensureSelection)
    function toggleHistory(id) {
        var next = selectedHistoryIds.slice()
        var at = next.indexOf(id)
        if (at >= 0) next.splice(at, 1); else next.push(id)
        selectedHistoryIds = next
    }
    function toggleFavorite(id) {
        var next = selectedFavoriteIds.slice()
        var at = next.indexOf(id)
        if (at >= 0) next.splice(at, 1); else next.push(id)
        selectedFavoriteIds = next
    }
    function play(kind, id, mode) {
        playingEntry = {kind: kind, id: id, mode: mode}
        facade.requestPlayback(mode, {kind: kind, id: id})
    }
    function editWork(patch) {
        var row = selectedRow()
        if (selectedKind === "task" && row) facade.command("setConfig", {taskId: row.taskId, revision: row.revision, patch: patch})
    }
    function resolveSubmission() {
        if (submitted.some(function(item) { return item.status === "conflict" })) { conflictDialog.open(); return }
        if (submitted.some(function(item) { return item.status === "ready" })) facade.command("commitSubmission", {submissionId: submissionId})
        else {
            facade.command("abandonSubmission", {submissionId: submissionId})
            submissionId = ""
            if (duplicateRecords.length) duplicateDialog.open()
        }
    }
    function batchApply(output, size) {
        var patch = {}
        if (output) {
            patch.outputDirectoryRule = batchSource ? "source_directory" : "explicit_directory"
            patch.explicitOutputDirectory = batchSource ? null : batchDirectory
        }
        if (size) patch.batchSize = batchSize
        facade.command("setCheckedConfig", {patch: patch, queryToken: data.work.queryToken})
    }
    function moveWork(source, target) {
        if (source !== target) facade.command("moveWork", {source: source, target: target})
    }
    function hasMore() {
        if (page === "select") return false
        if (page === "running") return false
        if (libraryTab === "history") return extraHistory.length ? !!extraHistoryCursor : !!data.history.nextCursor
        return extraFavorites.length ? !!extraFavoritesCursor : !!data.favorites.nextCursor
    }
    function loadMore() {
        if (libraryTab === "history") facade.command("pageHistory", {after: extraHistory.length ? extraHistoryCursor : data.history.nextCursor, queryToken: data.history.queryToken})
        else facade.command("pageFavorites", {after: extraFavorites.length ? extraFavoritesCursor : data.favorites.nextCursor, queryToken: data.favorites.queryToken})
    }
    function filterHistory(queryText, status) {
        extraHistory = []; extraHistoryCursor = null; selectedHistoryIds = []
        historyStatusValue = status
        facade.command("setHistoryFilter", {query: queryText, status: status})
    }
    function filterFavorites(queryText, sort, collectionId) {
        extraFavorites = []; extraFavoritesCursor = null
        selectedFavoriteIds = []
        favoriteChip = collectionId
        facade.command("setFavoriteFilter", {query: queryText, sort: sort, collection: collectionId})
    }
    function moveQueued(source, target) {
        var queued = data.active.filter(function(row) { return row.status === "queued" })
        var ids = queued.map(function(row) { return row.attempt_id })
        var from = ids.indexOf(source), to = ids.indexOf(target)
        if (from < 0 || to < 0 || from === to) return
        ids.splice(from, 1); ids.splice(to, 0, source)
        var revisions = {}
        queued.forEach(function(row) { revisions[row.attempt_id] = row.revision })
        facade.command("reorderQueued", {ids: ids, revisions: revisions})
    }
    function isBlack(note) { return [1,3,6,8,10].indexOf(note % 12) >= 0 }
    function whitesBefore(note) { var n = 0; for (var i = 21; i < note; i++) if (!isBlack(i)) n++; return n }
    property var whiteNotes: { var a = []; for (var i = 21; i <= 108; i++) if (!isBlack(i)) a.push(i); return a }
    property var blackNotes: { var a = []; for (var i = 21; i <= 108; i++) if (isBlack(i)) a.push(i); return a }

    // ── 控件（对应 .btn / .box / .led / .star / .mute / .edit）────────
    component WorkButton: Button {
        id: control
        property bool primary: false
        property bool small: false
        property bool ghost: false
        property bool nav: false
        property bool current: false
        property int boxRadius: 2
        implicitHeight: small ? 27 : 32
        leftPadding: small ? 10 : 14
        rightPadding: small ? 10 : 14
        font.pixelSize: small ? 11 : 12
        contentItem: Text {
            text: control.text
            font: control.font
            color: {
                if (!control.enabled) return control.primary || control.current ? "#f0f0f0" : root.faint
                if (control.primary || control.current) return "white"
                if (control.nav) return control.highlighted ? root.ink : (control.hovered ? root.ink : root.muted)
                if (control.ghost) return control.hovered ? root.ink : root.muted
                return root.ink
            }
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
        }
        background: Rectangle {
            radius: control.boxRadius
            color: {
                if (control.nav) return control.highlighted ? root.paper : (control.down ? "#e8e8e8" : control.hovered ? "#f6f6f6" : "transparent")
                if (control.primary || control.current)
                    return !control.enabled ? "#c4c4c4" : (control.down || control.hovered ? "#3a3a3a" : root.accent)
                if (control.ghost) return control.down || control.hovered ? "#e8e8e8" : "transparent"
                return control.down ? "#e8e8e8" : control.hovered ? "#f4f4f4" : root.paper
            }
            border.width: control.ghost && !control.hovered && !control.down ? 1 : 1
            border.color: {
                if (control.primary || control.current) return control.enabled ? root.accent : "#c4c4c4"
                if (control.nav) return control.highlighted ? root.strongRule : "transparent"
                if (control.ghost) return control.hovered || control.down ? root.strongRule : "transparent"
                return control.enabled ? (control.hovered ? "#7d7d7d" : root.strongRule) : root.rule
            }
        }
    }

    component WorkCheck: CheckBox {
        id: check
        implicitWidth: 22
        implicitHeight: 24
        padding: 0
        indicator: Rectangle {
            width: 14; height: 14; radius: 2
            anchors.centerIn: parent
            color: check.checked ? root.accent : root.fill
            border.color: check.checked ? root.accent : root.strongRule
            Text {
                anchors.centerIn: parent
                text: "✓"; color: "white"; font.pixelSize: 10; font.bold: true
                visible: check.checked
            }
        }
        contentItem: Item {}
    }

    component Led: Rectangle {
        property string kind: ""
        width: 8; height: 8; radius: 4
        border.width: 1
        border.color: kind === "ready" || kind === "done" ? root.okLedEdge
            : kind === "run" ? "#2a2a2a"
            : kind === "fail" ? "#9c4f4f"
            : kind === "cancel" ? "#8d8d8d" : root.strongRule
        color: kind === "ready" || kind === "done" ? root.okLed
            : kind === "run" ? "#4a4a4a"
            : kind === "fail" ? "#c07070"
            : kind === "cancel" ? "#b0b0b0" : "#cfcfcf"
    }

    component StarButton: Button {
        id: star
        property bool on: false
        implicitWidth: 24
        implicitHeight: 24
        padding: 0
        contentItem: Text {
            text: star.text
            font.pixelSize: 14
            color: star.on ? root.accent : "#b0b0b0"
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
        }
        background: Rectangle {
            radius: 2
            color: star.down ? "#dcdcdc" : star.hovered ? "#e8e8e8" : "transparent"
            border.color: star.hovered ? root.strongRule : "transparent"
        }
    }

    // 线性小图标：铅笔 / 扬声器（对应 HTML 的内联 SVG）
    component IconCanvas: Canvas {
        property string kind: "pencil"
        property color strokeColor: root.ink
        width: 15; height: 15
        antialiasing: true
        onStrokeColorChanged: requestPaint()
        onPaint: {
            var ctx = getContext("2d")
            ctx.reset()
            ctx.strokeStyle = String(strokeColor)
            ctx.lineWidth = 1.6
            ctx.lineCap = "round"
            ctx.lineJoin = "round"
            if (kind === "pencil") {
                ctx.beginPath(); ctx.moveTo(3, 13); ctx.lineTo(13.5, 13); ctx.stroke()
                ctx.beginPath(); ctx.moveTo(12.4, 1.9); ctx.lineTo(3.8, 10.5); ctx.stroke()
                ctx.beginPath(); ctx.moveTo(3.8, 10.5); ctx.lineTo(2.6, 13.4); ctx.lineTo(5.5, 12.2); ctx.closePath(); ctx.stroke()
            } else if (kind === "speaker") {
                ctx.beginPath()
                ctx.moveTo(6.5, 3); ctx.lineTo(3.5, 6); ctx.lineTo(1, 6)
                ctx.lineTo(1, 10); ctx.lineTo(3.5, 10); ctx.lineTo(6.5, 13)
                ctx.closePath(); ctx.stroke()
                ctx.beginPath(); ctx.arc(7, 8, 3, -0.85, 0.85); ctx.stroke()
                ctx.beginPath(); ctx.arc(7, 8, 5.4, -0.85, 0.85); ctx.stroke()
            } else {
                ctx.beginPath()
                ctx.moveTo(6.5, 3); ctx.lineTo(3.5, 6); ctx.lineTo(1, 6)
                ctx.lineTo(1, 10); ctx.lineTo(3.5, 10); ctx.lineTo(6.5, 13)
                ctx.closePath(); ctx.stroke()
                ctx.beginPath(); ctx.moveTo(9.5, 5.5); ctx.lineTo(14, 10.5); ctx.stroke()
                ctx.beginPath(); ctx.moveTo(14, 5.5); ctx.lineTo(9.5, 10.5); ctx.stroke()
            }
        }
    }

    component PencilButton: Button {
        id: pencil
        property string tip: ""
        implicitWidth: 24
        implicitHeight: 24
        padding: 0
        opacity: enabled ? 1 : 0.35
        contentItem: Item {
            IconCanvas {
                anchors.centerIn: parent
                kind: "pencil"
                strokeColor: pencil.down ? root.ink : pencil.hovered ? root.accent : "#5c5c5c"
            }
        }
        background: Rectangle {
            radius: 2
            color: pencil.down ? "#dcdcdc" : pencil.hovered ? "#e8e8e8" : "transparent"
            border.color: pencil.hovered ? root.strongRule : "transparent"
        }
        ToolTip.visible: hovered && tip.length > 0 && enabled
        ToolTip.text: tip
    }

    component MuteButton: Button {
        id: muteBtn
        property bool muted: false
        implicitWidth: 22
        implicitHeight: 22
        padding: 0
        contentItem: Item {
            IconCanvas {
                anchors.centerIn: parent
                kind: muteBtn.muted ? "speaker_off" : "speaker"
                strokeColor: muteBtn.muted ? "white" : "#4a4a4a"
            }
        }
        background: Rectangle {
            radius: 2
            color: muteBtn.muted ? root.accent : (muteBtn.down ? "#dcdcdc" : muteBtn.hovered ? "#e8e8e8" : "transparent")
            border.color: !muteBtn.muted && muteBtn.hovered ? root.strongRule : "transparent"
        }
        ToolTip.visible: hovered
        ToolTip.text: muted ? "取消静音" : "静音"
    }

    component FieldLabel: Label {
        color: root.faint
        font.family: "Microsoft YaHei UI"
        font.pixelSize: 10
        font.weight: Font.Normal
        renderType: Text.NativeRendering
    }

    component ValBox: Rectangle {
        property string text: ""
        implicitHeight: valText.implicitHeight + 14
        color: root.fill
        border.color: root.rule
        radius: 2
        Label {
            id: valText
            anchors.fill: parent
            anchors.margins: 7
            text: parent.text
            color: root.ink
            font.family: root.mono
            font.pixelSize: 11
            wrapMode: Text.WrapAnywhere
        }
    }

    component PlaybackLane: RowLayout {
        id: lane
        property string track: "midi"
        property real seekPreviewSeconds: -1
        readonly property real shownPosition: seekPreviewSeconds >= 0 ? seekPreviewSeconds : (root.player.positionSeconds || 0)
        readonly property bool hasSession: !!root.player.sessionId
        readonly property bool active: hasSession && (root.player.mode === track || root.player.mode === "paired")
        function secondsAt(x) {
            return Math.max(0, Math.min(1, x / trackBar.width)) * Math.max(1, root.player.durations[track] || 0)
        }
        visible: !hasSession ? track === "midi" : (root.player.mode === track || root.player.mode === "paired")
        spacing: 9
        Label {
            text: track === "audio" ? "音频" : "MIDI"
            Layout.preferredWidth: 30
            color: root.muted; font.pixelSize: 10; font.letterSpacing: 0.6
        }
        Label {
            text: root.shortTime(active ? lane.shownPosition : 0)
            color: root.muted; font.pixelSize: 11; font.family: root.mono
        }
        Item {
            id: trackBar
            Layout.fillWidth: true
            Layout.minimumWidth: 60
            Layout.preferredHeight: 18
            Rectangle {
                anchors.fill: parent
                radius: 2
                color: active ? root.paper : root.fill
                border.color: root.strongRule
                Rectangle {
                    width: parent.width * (active ? Math.min(1, lane.shownPosition / Math.max(1, root.player.durations[track] || 0)) : 0)
                    height: parent.height
                    color: "#dcdcdc"
                    Rectangle { anchors.right: parent.right; width: 1; height: parent.height; visible: parent.width > 0; color: "#7d7d7d" }
                }
            }
            MouseArea {
                anchors.fill: parent
                enabled: active
                cursorShape: active ? Qt.PointingHandCursor : Qt.ArrowCursor
                onPressed: function(mouse) { lane.seekPreviewSeconds = lane.secondsAt(mouse.x) }
                onPositionChanged: function(mouse) { if (pressed) lane.seekPreviewSeconds = lane.secondsAt(mouse.x) }
                onReleased: function(mouse) {
                    var seconds = lane.secondsAt(mouse.x)
                    lane.seekPreviewSeconds = -1
                    facade.seekPlayback(seconds)
                }
                onCanceled: lane.seekPreviewSeconds = -1
            }
        }
        Label {
            text: active ? root.shortTime(root.player.durations[track] || 0) : "--:--"
            color: root.muted; font.pixelSize: 11; font.family: root.mono
        }
        MuteButton {
            muted: root.player.muted[track] === true
            enabled: active
            onClicked: facade.setMuted(track, !root.player.muted[track])
        }
        Slider {
            id: fader
            Layout.preferredWidth: 132
            from: 0; to: 1
            value: root.player.muted[track] ? 0 : root.player.volumes[track]
            enabled: active
            opacity: enabled ? 1 : 0.45
            onMoved: facade.setVolume(track, value)
            background: Rectangle {
                y: fader.topPadding + fader.availableHeight / 2 - height / 2
                x: fader.leftPadding
                width: fader.availableWidth
                height: 4; radius: 2
                color: "#cfcfcf"
            }
            handle: Rectangle {
                x: fader.leftPadding + fader.visualPosition * fader.availableWidth - width / 2
                y: fader.topPadding + fader.availableHeight / 2 - height / 2
                width: 11; height: 15; radius: 1
                color: "#4a4a4a"
            }
        }
        Label {
            Layout.preferredWidth: 36
            text: Math.round((root.player.muted[track] ? 0 : root.player.volumes[track]) * 100) + "%"
            color: root.player.muted[track] ? "#b4b4b4" : root.muted
            font.pixelSize: 10
            font.family: root.mono
            horizontalAlignment: Text.AlignRight
        }
    }

    // ── facade 信号接线 ──────────────────────────────────────────────
    Connections {
        target: facade
        function onProjectionChanged() {
            var revision = facade.projection.revision
            if (revision !== root.seenRevision) {
                root.seenRevision = revision
                root.extraHistory = []; root.extraFavorites = []
                root.extraHistoryCursor = null; root.extraFavoritesCursor = null
            }
            Qt.callLater(root.ensureSelection)
        }
        function onIssue(message) { root.statusText = message }
        function onPathChosen(path) {
            if (root.outputPickerTarget === "batch") { root.batchDirectory = path; root.batchSource = false }
            else root.editWork({outputDirectoryRule: "explicit_directory", explicitOutputDirectory: path})
            root.outputPickerTarget = "task"
        }
        function onNoteStarted(note, velocity) { var n = Object.assign({}, root.notes); n[note] = velocity; root.notes = n }
        function onNoteEnded(note) { var n = Object.assign({}, root.notes); delete n[note]; root.notes = n }
        function onPedalChanged(down) { root.pedalDown = down }
        function onNotesCleared() { root.notes = {}; root.pedalDown = false }
        function onCommandFinished(requestId, command, result) {
            if (command === "prepareSubmission") root.preparingSubmission = false
            if (!result.ok) {
                if (command === "commitSubmission") {
                    if (root.submissionId) facade.command("abandonSubmission", {submissionId: root.submissionId})
                    root.submissionId = ""
                    root.submitted = []
                    root.statusText = result.error + "；请重新预检"
                } else root.statusText = result.error
                if (command === "decideConflict" || command === "previewConflictMidi") {
                    root.conflictError = result.error
                    conflictDialog.open()
                }
                return
            }
            if (command === "importAudio") {
                var succeeded = result.data.skipped.filter(function(item) { return item.code === "already_transcribed" }).length
                root.statusText = succeeded
                    ? "已有成功结果 " + succeeded + " 项，未导入；新导入 " + result.data.imported.length + " 项"
                    : "导入 " + result.data.imported.length + " 项；跳过 " + result.data.skipped.length + " 项"
            }
            else if (command === "prepareSubmission") {
                root.conflictError = ""
                root.submissionId = result.data.submissionId
                root.submitted = result.data.items
                root.duplicateRecords = result.data.items.filter(function(item) { return item.status === "already_transcribed" })
                if (root.duplicateRecords.length) root.statusText = root.duplicateRecords.length + " 项已有成功结果"
                root.resolveSubmission()
            } else if (command === "decideConflict") {
                var changed = result.data.items || [result.data]
                root.submitted = root.submitted.map(function(item) {
                    return changed.find(function(row) { return row.taskId === item.taskId }) || item
                })
                root.resolveSubmission()
            } else if (command === "commitSubmission") {
                root.submissionId = ""; root.page = "running"
                root.statusText = "已提交 " + result.data.queued.length + " 项"
                if (root.duplicateRecords.length) duplicateDialog.open()
            } else if (command === "addFavorite") {
                root.statusText = "★ 已收藏"
                root.toastActionText = "分到收藏夹…"
                root.toastFavoriteId = result.data.favoriteId || ""
            } else if (command === "removeFavorites") {
                root.statusText = "已移出收藏"
                root.selectedFavoriteIds = []
            } else if (command === "requestPlayback" && result.data.state === "needs_file_resolution") {
                root.fileCheck = result.data; fileDialog.open()
            } else if (command === "requestPlayback" && result.data.state === "needs_unverified_confirmation") {
                root.unverifiedRequestId = requestId; unverifiedDialog.open()
            } else if (command === "relocate" || command === "reassignInput") {
                root.play(root.playingEntry.kind, root.playingEntry.id, root.playingEntry.mode)
            } else if (command === "previewDeleteHistory") {
                root.fileCheck = result.data; deleteDialog.open()
            } else if (command === "previewRequeue") {
                root.fileCheck = result.data; requeueDialog.open()
            } else if (command === "inspectResult") {
                root.inspectedResult = result.data
                resultDialog.open()
            } else if (command === "beginRecovery") {
                facade.command("createRecoveryWork", {token: result.data})
            } else if (command === "createRecoveryWork") {
                resultDialog.close(); root.page = "select"
                root.statusText = "已加入待提交区，可检查配置后提交"
            } else if (command === "relocateSuccessInput") {
                facade.command("inspectResult", {successRecordId: root.inspectedResult.successRecordId})
            } else if (command === "removeCheckedWork") {
                root.statusText = "已移除 " + result.data + " 项"
            } else if (command === "confirmDeleteHistory") {
                root.selectedHistoryIds = []
                root.select("", "")
            } else if (command === "deleteCollection") {
                root.favoriteChip = "all"
                root.filterFavorites(favoriteQuery.text, favoriteSort.currentIndex === 0 ? "favorited" : "transcribed", "all")
            } else if (command === "pageHistory") {
                root.extraHistory = root.extraHistory.concat(result.data.items)
                root.extraHistoryCursor = result.data.nextCursor
            } else if (command === "pageFavorites") {
                root.extraFavorites = root.extraFavorites.concat(result.data.items)
                root.extraFavoritesCursor = result.data.nextCursor
            }
        }
    }

    onClosing: function(event) {
        if (root.data.active.length) { event.accepted = false; closeDialog.open() }
        else Qt.quit()
    }

    // ── 外壳三段：顶栏 / 主体 / 播放器（anchors 契约）──────────────────
    Item {
        anchors.fill: parent

        // 顶栏（.top，60px）
        Rectangle {
            id: header
            anchors.top: parent.top
            anchors.left: parent.left
            anchors.right: parent.right
            height: 60
            color: root.shell
            Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: root.strongRule }
            Row {
                anchors.left: parent.left
                anchors.leftMargin: 20
                anchors.verticalCenter: parent.verticalCenter
                spacing: 26
                Label {
                    text: "PIANOTRANS"
                    color: root.ink
                    font.pixelSize: 14
                    font.bold: true
                    font.letterSpacing: 3.1
                    anchors.verticalCenter: parent.verticalCenter
                }
                Row {
                    spacing: 2
                    anchors.verticalCenter: parent.verticalCenter
                    WorkButton { text: "选择音频"; nav: true; highlighted: root.page === "select"; onClicked: root.page = "select" }
                    WorkButton { text: "转录中"; nav: true; highlighted: root.page === "running"; onClicked: root.page = "running" }
                    WorkButton { text: "历史"; nav: true; highlighted: root.page === "library"; onClicked: root.page = "library" }
                }
            }
            Row {
                anchors.right: parent.right
                anchors.rightMargin: 20
                anchors.verticalCenter: parent.verticalCenter
                spacing: 14
                Button {
                    id: engineButton
                    implicitHeight: 27
                    padding: 0
                    leftPadding: 8; rightPadding: 8
                    contentItem: Row {
                        spacing: 7
                        Led { kind: "ready"; anchors.verticalCenter: parent.verticalCenter }
                        Label {
                            text: "引擎就绪 · " + (root.data.deviceMode === "auto" ? "自动" : root.data.deviceMode === "cpu" ? "仅 CPU" : "仅 GPU") + "（" + (root.data.detectedDevice === "cuda" ? "CUDA" : "CPU") + "）"
                            color: root.muted; font.pixelSize: 11
                            anchors.verticalCenter: parent.verticalCenter
                        }
                    }
                    background: Rectangle {
                        radius: 2
                        color: engineButton.down ? "#e8e8e8" : engineButton.hovered ? "#f4f4f4" : root.paper
                        border.color: engineButton.hovered ? "#7d7d7d" : root.strongRule
                    }
                    onClicked: settingsDialog.open()
                }
                WorkButton { text: "设置"; small: true; onClicked: settingsDialog.open() }
            }
        }

        // 主体（.body：队列 + 检查器）
        Item {
            id: body
            anchors.top: header.bottom
            anchors.bottom: player.top
            anchors.left: parent.left
            anchors.right: parent.right

            // 左：队列（.queue）
            Item {
                id: queueArea
                anchors.top: parent.top
                anchors.bottom: parent.bottom
                anchors.left: parent.left
                anchors.right: root.width > 880 ? focusPanel.left : parent.right
                clip: true

                ColumnLayout {
                    anchors.fill: parent
                    anchors.leftMargin: 22
                    anchors.rightMargin: 22
                    anchors.topMargin: 18
                    anchors.bottomMargin: 14
                    spacing: 10

                    // 标题行（.qhead）
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 12
                        Label {
                            text: root.page === "select" ? "选择音频" : root.page === "running" ? "转录中" : root.libraryTab === "history" ? "历史" : "收藏夹"
                            color: root.ink
                            font.family: "Microsoft YaHei UI"
                            font.pixelSize: 19
                            font.weight: Font.Normal
                            Layout.alignment: Qt.AlignVCenter
                        }
                        Label {
                            Layout.fillWidth: true
                            Layout.alignment: Qt.AlignVCenter
                            text: root.page === "select" ? root.data.work.totalCount + " 项"
                                : root.page === "running" ? (root.data.active.filter(function(row) { return row.status === "running" }).length) + " 项运行 · " + (root.data.active.filter(function(row) { return row.status === "queued" }).length) + " 项等待"
                                : root.libraryTab === "history" ? root.data.history.totalCount + " 条记录 · 只存元数据，不复制文件"
                                : root.data.favorites.totalCount + " 个收藏 · " + root.data.collections.length + " 个收藏夹"
                            color: root.faint
                            font.pixelSize: 11
                            elide: Text.ElideRight
                        }
                        Row {
                            visible: root.page === "library"
                            spacing: 0
                            Layout.alignment: Qt.AlignVCenter
                            WorkButton { text: "历史记录"; small: true; primary: root.libraryTab === "history"; boxRadius: 0; onClicked: root.libraryTab = "history" }
                            WorkButton { text: "收藏夹"; small: true; primary: root.libraryTab === "favorites"; boxRadius: 0; onClicked: root.libraryTab = "favorites" }
                        }
                    }

                    // 选择音频工具条
                    RowLayout {
                        visible: root.page === "select"
                        Layout.fillWidth: true
                        spacing: 7
                        WorkButton { text: "导入文件"; small: true; Layout.alignment: Qt.AlignVCenter; onClicked: facade.pickFiles() }
                        WorkButton { text: "导入文件夹"; small: true; Layout.alignment: Qt.AlignVCenter; onClicked: facade.pickFolder() }
                        Rectangle { Layout.preferredWidth: 1; Layout.preferredHeight: 20; Layout.alignment: Qt.AlignVCenter; color: root.rule }
                        WorkButton {
                            text: root.data.work.totalCount && root.data.work.checkedCount === root.data.work.totalCount ? "取消全选" : "全选"
                            small: true; enabled: root.data.work.totalCount > 0
                            Layout.alignment: Qt.AlignVCenter
                            onClicked: facade.command("setAllChecked", {checked: root.data.work.checkedCount !== root.data.work.totalCount})
                        }
                        WorkButton {
                            visible: root.data.work.pageCount > 1
                            text: "移除本页勾选项"; small: true; enabled: root.pageCheckedIds().length > 0
                            Layout.alignment: Qt.AlignVCenter
                            onClicked: facade.command("removeCheckedWork", {scope: "page", ids: root.pageCheckedIds(), queryToken: root.data.work.queryToken})
                        }
                        WorkButton {
                            text: "移除勾选项"; small: true; enabled: root.data.work.checkedCount > 0
                            Layout.alignment: Qt.AlignVCenter
                            onClicked: facade.command("removeCheckedWork", {scope: "all"})
                        }
                        Rectangle { Layout.preferredWidth: 1; Layout.preferredHeight: 20; Layout.alignment: Qt.AlignVCenter; color: root.rule }
                        Label { text: "已选中 " + (root.data.work.checkedCount || 0) + " 个"; color: root.muted; font.pixelSize: 11; Layout.alignment: Qt.AlignVCenter }
                        WorkButton { text: "批量配置…"; small: true; enabled: root.data.work.checkedCount > 0; Layout.alignment: Qt.AlignVCenter; onClicked: batchDialog.open() }
                        Item { Layout.fillWidth: true; Layout.preferredHeight: 1 }
                        WorkButton {
                            text: "开始转录"
                            primary: true
                            enabled: !root.preparingSubmission && !root.submissionId && root.data.work.checkedCount > 0
                            Layout.alignment: Qt.AlignVCenter
                            onClicked: { root.preparingSubmission = true; facade.command("prepareSubmission", {}) }
                        }
                    }

                    // 历史工具条（搜索 + 状态筛选 + 多选动作条）
                    RowLayout {
                        visible: root.page === "library" && root.libraryTab === "history"
                        Layout.fillWidth: true
                        spacing: 7
                        TextField {
                            id: query
                            Layout.preferredWidth: 150
                            Layout.preferredHeight: 27
                            placeholderText: "搜索文件名…"
                            placeholderTextColor: root.faint
                            color: root.ink
                            selectionColor: root.sel
                            selectedTextColor: root.ink
                            font.family: "Microsoft YaHei UI"
                            font.pixelSize: 12
                            selectByMouse: true
                            background: Rectangle { radius: 2; border.color: root.strongRule; color: root.paper }
                            onTextEdited: historySearchTimer.restart()
                            onAccepted: root.filterHistory(text, root.historyStatusValue)
                        }
                        Timer { id: historySearchTimer; interval: 300; onTriggered: root.filterHistory(query.text, root.historyStatusValue) }
                        Rectangle { Layout.preferredWidth: 1; Layout.preferredHeight: 20; Layout.alignment: Qt.AlignVCenter; color: root.rule }
                        Repeater {
                            model: [{label: "全部", value: "all"}, {label: "已完成", value: "succeeded"}, {label: "失败", value: "failed"}, {label: "已取消", value: "cancelled"}]
                            delegate: WorkButton {
                                required property var modelData
                                small: true
                                text: modelData.label
                                primary: root.historyStatusValue === modelData.value
                                Layout.alignment: Qt.AlignVCenter
                                onClicked: root.filterHistory(query.text, modelData.value)
                            }
                        }
                        Rectangle { Layout.preferredWidth: 1; Layout.preferredHeight: 20; Layout.alignment: Qt.AlignVCenter; color: root.rule }
                        WorkButton { text: "清理历史…"; small: true; enabled: root.data.history.totalCount > 0; Layout.alignment: Qt.AlignVCenter; onClicked: facade.command("previewDeleteHistory", {}) }
                    }

                    // 收藏工具条（搜索 + 分组 chips + 排序 + 分组管理）
                    RowLayout {
                        visible: root.page === "library" && root.libraryTab === "favorites"
                        Layout.fillWidth: true
                        spacing: 7
                        TextField {
                            id: favoriteQuery
                            Layout.preferredWidth: 150
                            Layout.preferredHeight: 27
                            placeholderText: "搜索文件名…"
                            placeholderTextColor: root.faint
                            color: root.ink
                            selectionColor: root.sel
                            selectedTextColor: root.ink
                            font.family: "Microsoft YaHei UI"
                            font.pixelSize: 12
                            selectByMouse: true
                            background: Rectangle { radius: 2; border.color: root.strongRule; color: root.paper }
                            onTextEdited: favoriteSearchTimer.restart()
                            onAccepted: root.filterFavorites(text, favoriteSort.currentIndex === 0 ? "favorited" : "transcribed", root.favoriteChip)
                        }
                        Timer { id: favoriteSearchTimer; interval: 300; onTriggered: root.filterFavorites(favoriteQuery.text, favoriteSort.currentIndex === 0 ? "favorited" : "transcribed", root.favoriteChip) }
                        Rectangle { Layout.preferredWidth: 1; Layout.preferredHeight: 20; Layout.alignment: Qt.AlignVCenter; color: root.rule }
                        Repeater {
                            model: [{label: "全部", value: "all"}, {label: "未分类", value: "unclassified"}].concat(root.data.collections.map(function(row) { return {label: row.name, value: row.collection_id} }))
                            delegate: Button {
                                required property var modelData
                                id: chip
                                Layout.preferredHeight: 25
                                padding: 0
                                leftPadding: 10; rightPadding: 10
                                contentItem: Text {
                                    text: chip.modelData.label
                                    color: chip.checked ? "white" : "#3d3d3d"
                                    font.pixelSize: 11
                                    anchors.verticalCenter: parent.verticalCenter
                                }
                                checked: root.favoriteChip === chip.modelData.value
                                background: Rectangle {
                                    radius: 12
                                    color: chip.checked ? root.accent : chip.hovered ? "#f2f2f2" : root.paper
                                    border.color: chip.checked ? root.accent : root.strongRule
                                }
                                onClicked: root.filterFavorites(favoriteQuery.text, favoriteSort.currentIndex === 0 ? "favorited" : "transcribed", chip.modelData.value)
                            }
                        }
                        ComboBox {
                            id: favoriteSort
                            Layout.preferredHeight: 27
                            Layout.preferredWidth: 118
                            font.pixelSize: 11
                            model: ["收藏时间 ↓", "转录时间 ↓"]
                            background: Rectangle { radius: 2; border.color: root.strongRule; color: root.paper }
                            onActivated: root.filterFavorites(favoriteQuery.text, currentIndex === 0 ? "favorited" : "transcribed", root.favoriteChip)
                        }
                        WorkButton {
                            visible: root.favoriteChip !== "all" && root.favoriteChip !== "unclassified"
                            text: "重命名"; small: true
                            Layout.alignment: Qt.AlignVCenter
                            onClicked: {
                                var found = root.data.collections.find(function(row) { return row.collection_id === root.favoriteChip })
                                if (!found) return
                                root.editingCollectionId = found.collection_id
                                root.editingCollectionRevision = found.revision
                                collectionName.text = found.name
                                collectionDialog.open()
                            }
                        }
                        WorkButton {
                            visible: root.favoriteChip !== "all" && root.favoriteChip !== "unclassified"
                            text: "解散收藏夹"; small: true
                            Layout.alignment: Qt.AlignVCenter
                            onClicked: facade.command("deleteCollection", {id: root.favoriteChip})
                        }
                        Item { Layout.fillWidth: true; Layout.preferredHeight: 1 }
                        WorkButton { text: "＋ 新建收藏夹"; small: true; Layout.alignment: Qt.AlignVCenter; onClicked: { root.editingCollectionId = ""; collectionName.clear(); collectionDialog.open() } }
                    }

                    // 多选动作条（.bulkbar，只在有勾选时出现）
                    Rectangle {
                        visible: root.page === "library"
                                 && ((root.libraryTab === "history" && root.selectedHistoryIds.length > 0)
                                     || (root.libraryTab === "favorites" && root.selectedFavoriteIds.length > 0))
                        Layout.preferredWidth: bulkRow.width + 18
                        Layout.preferredHeight: 31
                        radius: 2
                        color: root.fill
                        border.color: root.strongRule
                        Row {
                            id: bulkRow
                            anchors.centerIn: parent
                            spacing: 6
                            Label {
                                text: root.libraryTab === "history"
                                      ? "已选中 " + root.selectedHistoryIds.length + " 条"
                                      : "已选中 " + root.selectedFavoriteIds.length + " 条"
                                color: root.muted; font.pixelSize: 10
                                anchors.verticalCenter: parent.verticalCenter
                            }
                            WorkButton {
                                visible: root.libraryTab === "history"
                                text: "重新提交"; small: true
                                anchors.verticalCenter: parent.verticalCenter
                                onClicked: facade.command("previewRequeue", {ids: root.selectedHistoryIds})
                            }
                            WorkButton {
                                visible: root.libraryTab === "history"
                                text: "删除记录"; small: true
                                anchors.verticalCenter: parent.verticalCenter
                                onClicked: facade.command("previewDeleteHistory", {ids: root.selectedHistoryIds})
                            }
                            WorkButton {
                                visible: root.libraryTab === "favorites"
                                text: "加入收藏夹…"; small: true; enabled: root.data.collections.length > 0
                                anchors.verticalCenter: parent.verticalCenter
                                onClicked: bulkCollectionDialog.open()
                            }
                            WorkButton {
                                visible: root.libraryTab === "favorites"
                                text: "移出收藏"; small: true
                                anchors.verticalCenter: parent.verticalCenter
                                onClicked: facade.command("removeFavorites", {ids: root.selectedFavoriteIds})
                            }
                            WorkButton {
                                text: "取消选择"; small: true; ghost: true
                                anchors.verticalCenter: parent.verticalCenter
                                onClicked: { root.selectedHistoryIds = []; root.selectedFavoriteIds = [] }
                            }
                        }
                    }

                    // 索引盒 / 空状态
                    Item {
                        id: binHost
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        Rectangle {
                            id: binFrame
                            anchors.fill: parent
                            visible: root.rows().length > 0
                            color: root.well
                            border.color: root.rule
                            radius: 3
                        }
                        Label {
                            visible: root.rows().length > 0
                            x: 12; y: 5
                            text: root.page === "select" ? "File Index · 文件索引" : root.page === "running" ? "Queue · 执行队列" : root.libraryTab === "history" ? "Archive · 归档" : "Library · 我的库"
                            font.family: "Microsoft YaHei UI"; font.pixelSize: 10; font.weight: Font.Normal
                            color: root.faint
                            z: 2
                        }
                        ListView {
                            id: entries
                            visible: root.rows().length > 0
                            anchors.fill: parent
                            anchors.topMargin: 25
                            anchors.leftMargin: 10
                            anchors.rightMargin: 10
                            anchors.bottomMargin: 10
                            clip: true
                            spacing: 11
                            header: Item { width: entries.width; height: 8 }
                            model: root.rows()
                            delegate: Rectangle {
                                id: entry
                                required property var modelData
                                property var row: modelData
                                property string kind: root.currentKind()
                                property string entryId: root.idOf(row)
                                readonly property bool isSelected: root.selectedKind === kind && root.selectedId === entryId
                                readonly property bool isHover: rowMouse.containsMouse
                                readonly property bool isTask: kind === "task"
                                readonly property bool isActiveRow: kind === "active"
                                readonly property bool isQueuedRow: isActiveRow && row.status === "queued"
                                readonly property bool isHistoryRow: kind === "history"
                                readonly property bool isFavoriteRow: kind === "favorite"
                                readonly property bool draggable: isTask || isQueuedRow
                                readonly property bool starable: isHistoryRow && row.status === "succeeded"
                                width: ListView.view.width
                                height: 61
                                radius: 2
                                color: isSelected ? root.sel : isHover ? "#fdfdfd" : root.paper
                                border.color: root.strongRule
                                opacity: 1
                                Rectangle { visible: entry.isSelected; width: 3; height: parent.height; color: root.accent }
                                // 格式标签耳（.fmt）
                                Rectangle {
                                    x: 11; y: -8
                                    width: fmtLabel.implicitWidth + 14
                                    height: 15
                                    color: root.paper
                                    border.color: root.strongRule
                                    Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: entry.borderColorFix }
                                    Label {
                                        id: fmtLabel
                                        anchors.centerIn: parent
                                        text: root.extOf(entry.row)
                                        color: root.faint
                                        font.pixelSize: 9
                                        font.bold: true
                                        font.letterSpacing: 1.2
                                    }
                                }
                                property color borderColorFix: "transparent"
                                MouseArea {
                                    id: rowMouse
                                    anchors.fill: parent
                                    hoverEnabled: true
                                    acceptedButtons: Qt.LeftButton
                                    onClicked: root.select(entry.kind, entry.entryId)
                                    onDoubleClicked: {
                                        if (entry.kind === "task") root.play("task", entry.entryId, "audio")
                                        else if (entry.kind === "favorite" || (entry.kind === "history" && entry.row.status === "succeeded")) root.play(entry.kind, entry.entryId, "midi")
                                    }
                                }
                                RowLayout {
                                    anchors.fill: parent
                                    anchors.leftMargin: 13
                                    anchors.rightMargin: 13
                                    spacing: 11
                                    // 拖动把手
                                    Item {
                                        Layout.preferredWidth: 20
                                        Layout.fillHeight: true
                                        visible: entry.draggable
                                        Rectangle {
                                            anchors.centerIn: parent
                                            width: 20; height: 22
                                            radius: 2
                                            color: grip.containsMouse ? root.fill : "transparent"
                                            border.color: grip.containsMouse ? root.strongRule : "transparent"
                                        }
                                        Grid {
                                            anchors.centerIn: parent
                                            columns: 2
                                            rows: 3
                                            spacing: 2.5
                                            Repeater {
                                                model: 6
                                                delegate: Rectangle { width: 1.6; height: 4.5; radius: 0.8; color: root.muted }
                                            }
                                        }
                                    }
                                    // 勾选框
                                    WorkCheck {
                                        visible: entry.isTask || entry.isHistoryRow || entry.isFavoriteRow
                                        Layout.alignment: Qt.AlignVCenter
                                        checked: entry.isTask ? entry.row.checked === true
                                            : entry.isHistoryRow ? root.selectedHistoryIds.indexOf(entry.entryId) >= 0
                                            : root.selectedFavoriteIds.indexOf(entry.entryId) >= 0
                                        onClicked: {
                                            if (entry.isTask) facade.command("setChecked", {ids: [entry.entryId], checked: !entry.row.checked})
                                            else if (entry.isHistoryRow) root.toggleHistory(entry.entryId)
                                            else root.toggleFavorite(entry.entryId)
                                        }
                                    }
                                    // 名称
                                    ColumnLayout {
                                        Layout.fillWidth: true
                                        spacing: 3
                                        Label {
                                            Layout.fillWidth: true
                                            text: root.nameOf(entry.row)
                                            elide: Text.ElideMiddle
                                            font.family: "Microsoft YaHei UI"
                                            font.pixelSize: 13
                                            font.weight: Font.Normal
                                            color: root.ink
                                        }
                                        Label {
                                            Layout.fillWidth: true
                                            text: {
                                                if (entry.isTask) return root.directoryOf(entry.row)
                                                if (entry.isActiveRow) return entry.row.status === "queued" ? "第 " + (root.data.active.filter(function(r) { return r.status === "queued" }).indexOf(entry.row) + 1) + " 位 · 配置已提交" : "当前运行 · 无可用百分比"
                                                if (entry.isFavoriteRow) {
                                                    var tags = (entry.row.collectionIds || []).map(function(cid) {
                                                        var found = root.data.collections.find(function(c) { return c.collection_id === cid })
                                                        return found ? found.name : ""
                                                    }).filter(function(t) { return t.length > 0 })
                                                    var sortFav = root.favoriteSort ? root.favoriteSort.currentIndex === 0 : true
                                                    return (sortFav ? "收藏 " + root.stamp(entry.row.favorited_at) : "转录 " + root.stamp(entry.row.transcription_finished_at)) + (tags.length ? " · " + tags.join(" ") : "")
                                                }
                                                return root.recordTime(entry.row)
                                            }
                                            elide: Text.ElideMiddle
                                            font.pixelSize: 10
                                            font.family: root.mono
                                            color: root.faint
                                        }
                                    }
                                    // 状态
                                    Row {
                                        visible: entry.isTask || entry.isActiveRow || entry.isHistoryRow
                                        Layout.preferredWidth: 92
                                        Layout.alignment: Qt.AlignVCenter
                                        spacing: 7
                                        Led {
                                            kind: entry.isTask ? "ready" : root.ledKind(entry.row)
                                            anchors.verticalCenter: parent.verticalCenter
                                        }
                                        Label {
                                            text: entry.isTask ? "已就绪"
                                                : entry.isActiveRow ? (entry.row.status === "running" ? "转录中" : "等待中")
                                                : root.statusLabel(entry.row)
                                            color: entry.row.status === "failed" ? root.bad
                                                : entry.row.status === "cancelled" ? root.faint : "#4a4a4a"
                                            font.pixelSize: 11
                                            anchors.verticalCenter: parent.verticalCenter
                                        }
                                    }
                                    // 目标
                                    Label {
                                        Layout.preferredWidth: 78
                                        Layout.fillWidth: entry.isFavoriteRow || (entry.isHistoryRow && !entry.starable)
                                        Layout.alignment: Qt.AlignVCenter
                                        text: {
                                            if (entry.isTask) return entry.row.outputDirectoryRule === "source_directory" ? "同目录" : (entry.row.explicitOutputDirectory || "")
                                            if (entry.isFavoriteRow) return (entry.row.midi_path || "").split(/[\\/]/).pop()
                                            if (entry.isHistoryRow) return (entry.row.midi_path || "").split(/[\\/]/).pop()
                                            return (root.midiOf(entry.row) || "").split(/[\\/]/).pop()
                                        }
                                        elide: Text.ElideMiddle
                                        color: root.faint
                                        font.pixelSize: 10
                                        font.family: root.mono
                                        horizontalAlignment: Text.AlignRight
                                    }
                                    // 星标（只有已完成的历史行）
                                    StarButton {
                                        visible: entry.starable
                                        text: entry.row.favorite_id ? "★" : "☆"
                                        on: !!entry.row.favorite_id
                                        Layout.alignment: Qt.AlignVCenter
                                        ToolTip.visible: hovered
                                        ToolTip.text: on ? "取消收藏" : "收藏"
                                        onClicked: {
                                            if (entry.row.favorite_id) facade.command("removeFavorites", {ids: [entry.row.favorite_id]})
                                            else facade.command("addFavorite", {attemptId: entry.entryId})
                                        }
                                    }
                                }
                                // 悬停行内动作（.rowact：取消）
                                Rectangle {
                                    visible: entry.isActiveRow && entry.isHover
                                    anchors.right: parent.right
                                    anchors.rightMargin: 10
                                    anchors.verticalCenter: parent.verticalCenter
                                    width: 58
                                    height: 27
                                    color: root.paper
                                    border.color: root.strongRule
                                    radius: 2
                                    Label {
                                        anchors.centerIn: parent
                                        text: "取消"
                                        color: root.ink
                                        font.pixelSize: 11
                                    }
                                    MouseArea {
                                        anchors.fill: parent
                                        onClicked: facade.command("cancelAttempt", {attemptId: entry.entryId})
                                    }
                                }
                                Drag.active: grip.drag.active
                                Drag.source: entry
                                MouseArea {
                                    id: grip
                                    visible: entry.draggable
                                    x: 0
                                    width: 22
                                    height: parent.height
                                    drag.target: entry
                                    cursorShape: Qt.DragMoveCursor
                                    hoverEnabled: true
                                    onReleased: { entry.x = 0; entry.y = 0 }
                                }
                                DropArea {
                                    anchors.fill: parent
                                    onDropped: function(drop) {
                                        if (!drop.source || drop.source === entry) return
                                        if (entry.kind === "task") root.moveWork(drop.source.entryId, entry.entryId)
                                        else if (entry.kind === "active" && entry.row.status === "queued") root.moveQueued(drop.source.entryId, entry.entryId)
                                    }
                                }
                            }
                            footer: Item {
                                width: entries.width
                                height: entries.count === 0 ? 130 : root.hasMore() ? 46 : 0
                                Label {
                                    anchors.fill: parent
                                    visible: entries.count === 0
                                    text: root.page === "select" ? "导入音频后从这里开始" : root.page === "running" ? "当前没有转录任务" : "没有条目"
                                    horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter; color: root.muted
                                }
                                WorkButton { anchors.horizontalCenter: parent.horizontalCenter; visible: entries.count > 0 && root.hasMore(); text: "加载更多"; onClicked: root.loadMore() }
                            }
                        }
                        // 空状态（.empty，虚线框）
                        Rectangle {
                            anchors.fill: parent
                            anchors.topMargin: 14
                            visible: root.rows().length === 0
                            radius: 3
                            color: root.fill
                            Shape {
                                anchors.fill: parent
                                ShapePath {
                                    strokeColor: root.strongRule
                                    strokeWidth: 1
                                    strokeStyle: ShapePath.DashLine
                                    dashPattern: [4, 3]
                                    fillColor: "transparent"
                                    startX: 0.5; startY: 0.5
                                    PathLine { x: emptyBox.width - 0.5; y: 0.5 }
                                    PathLine { x: emptyBox.width - 0.5; y: emptyBox.height - 0.5 }
                                    PathLine { x: 0.5; y: emptyBox.height - 0.5 }
                                    PathLine { x: 0.5; y: 0.5 }
                                }
                            }
                            Item { id: emptyBox; anchors.fill: parent }
                            Column {
                                anchors.centerIn: parent
                                spacing: 6
                                Label {
                                    text: root.page === "select" ? "还没有待转录音频"
                                        : root.page === "running" ? "当前没有进行中的任务"
                                        : root.libraryTab === "history" ? (root.data.history.totalCount === 0 ? "历史为空" : "没有匹配的记录")
                                        : (root.data.favorites.totalCount === 0 ? "收藏夹还是空的" : "没有匹配的收藏")
                                    font.family: "Microsoft YaHei"
                                    font.pixelSize: 15
                                    font.weight: Font.Normal
                                    color: root.ink
                                    horizontalAlignment: Text.AlignHCenter
                                    width: parent.width
                                }
                                Label {
                                    font.family: "Microsoft YaHei"
                                    text: root.page === "select" ? "选择文件，或导入整个文件夹。"
                                        : root.page === "running" ? "在「选择音频」里勾选文件后开始转录。"
                                        : root.libraryTab === "history" ? (root.data.history.totalCount === 0 ? "完成的、失败的和已取消的任务都会归档到这里。" : "调整搜索词或状态筛选。")
                                        : (root.data.favorites.totalCount === 0 ? "在「历史记录」里点右上角的星，把已完成的结果收进来。" : "调整搜索词或分组。")
                                    color: root.muted
                                    font.pixelSize: 12
                                    horizontalAlignment: Text.AlignHCenter
                                    width: parent.width
                                }
                                Row {
                                    spacing: 8
                                    anchors.horizontalCenter: parent.horizontalCenter
                                    WorkButton { visible: root.page === "select"; text: "导入音频文件"; primary: true; onClicked: facade.pickFiles() }
                                    WorkButton { visible: root.page === "select"; text: "导入文件夹"; onClicked: facade.pickFolder() }
                                    WorkButton { visible: root.page === "running"; text: "返回选择音频"; onClicked: root.page = "select" }
                                    WorkButton { visible: root.page === "library" && root.libraryTab === "favorites" && root.data.favorites.totalCount === 0; text: "去看历史记录"; onClicked: root.libraryTab = "history" }
                                }
                                Label {
                                    visible: root.page === "select"
                                    text: "MP3 / WAV / FLAC / M4A / AAC / OGG / WMA"
                                    color: root.faint
                                    font.pixelSize: 10
                                    horizontalAlignment: Text.AlignHCenter
                                    width: parent.width
                                    topPadding: 10
                                }
                            }
                        }
                    }
                    // 分页（.pagination）
                    RowLayout {
                        id: paginationRow
                        visible: root.page === "select" && root.data.work.pageCount > 1
                        Layout.fillWidth: true
                        spacing: 5
                        Label {
                            text: ((root.data.work.page - 1) * root.data.work.pageSize + 1) + "–" + Math.min(root.data.work.page * root.data.work.pageSize, root.data.work.totalCount) + " / " + root.data.work.totalCount + " 项"
                            font.pixelSize: 11
                            color: root.muted
                            Layout.alignment: Qt.AlignVCenter
                        }
                        Item { Layout.fillWidth: true; Layout.preferredHeight: 1 }
                        WorkButton { text: "上一页"; small: true; enabled: root.data.work.page > 1; Layout.alignment: Qt.AlignVCenter; onClicked: facade.command("setWorkPage", {page: root.data.work.page - 1}) }
                        Repeater {
                            model: root.visibleWorkPages()
                            delegate: WorkButton {
                                required property int modelData
                                text: String(modelData)
                                small: true
                                current: modelData === root.data.work.page
                                Layout.alignment: Qt.AlignVCenter
                                onClicked: facade.command("setWorkPage", {page: modelData})
                            }
                        }
                        WorkButton { text: "下一页"; small: true; enabled: root.data.work.page < root.data.work.pageCount; Layout.alignment: Qt.AlignVCenter; onClicked: facade.command("setWorkPage", {page: root.data.work.page + 1}) }
                    }
                }
            }

            // 右：检查器（.focus）
            Rectangle {
                id: focusPanel
                visible: root.width > 880
                anchors.top: parent.top
                anchors.bottom: parent.bottom
                anchors.right: parent.right
                width: root.width <= 1080 ? 320 : 372
                color: root.paper
                Rectangle { anchors.left: parent.left; width: 1; height: parent.height; color: root.strongRule }
                ScrollView {
                    anchors.fill: parent
                    contentWidth: availableWidth
                    ColumnLayout {
                        width: parent.width
                        spacing: 0

                        Label {
                            text: root.selectedKind === "task" || root.selectedKind === "active" ? "SELECTED FILE · 已选文件"
                                : root.selectedKind === "history" ? "ARCHIVE RECORD · 历史记录"
                                : root.selectedKind === "favorite" ? "FAVORITE · 收藏"
                                : "Inspector · 检查器"
                            color: root.faint
                            font.family: "Microsoft YaHei UI"
                            font.pixelSize: 10
                            font.weight: Font.Normal
                            Layout.topMargin: 18
                            Layout.leftMargin: 20
                        }
                        Label {
                            Layout.fillWidth: true
                            Layout.leftMargin: 20
                            Layout.rightMargin: 20
                            Layout.topMargin: 8
                            text: root.selectedRow() ? root.nameOf(root.selectedRow()) : "未选中任何对象"
                            wrapMode: Text.WrapAnywhere
                            font.family: "Microsoft YaHei UI"
                            font.pixelSize: 16
                            font.weight: Font.Normal
                            color: root.ink
                        }
                        Label {
                            visible: root.selectedRow() === null
                            Layout.fillWidth: true
                            Layout.leftMargin: 20
                            Layout.rightMargin: 20
                            Layout.topMargin: 12
                            text: "点击左侧任意一行，这里显示它的路径、配置与操作。标题始终是文件名本身。"
                            wrapMode: Text.Wrap
                            font.pixelSize: 10
                            color: root.faint
                        }

                        // 读数窗（.readout）
                        Rectangle {
                            Layout.fillWidth: true
                            Layout.preferredHeight: 36
                            Layout.leftMargin: 20
                            Layout.rightMargin: 20
                            Layout.topMargin: 14
                            visible: root.selectedRow() !== null
                            color: root.fill
                            border.color: root.strongRule
                            radius: 2
                            RowLayout {
                                anchors.fill: parent
                                anchors.leftMargin: 11
                                anchors.rightMargin: 11
                                Label {
                                    text: {
                                        var row = root.selectedRow()
                                        if (!row) return ""
                                        var ext = root.extOf(row)
                                        if (root.selectedKind === "task") return ext + " · 已就绪"
                                        if (root.selectedKind === "favorite") return ext + " · 已收藏"
                                        return ext + " · " + root.statusLabel(row)
                                    }
                                    font.family: "Microsoft YaHei UI"
                                    font.pixelSize: 12
                                    font.weight: Font.Normal
                                    color: root.ink
                                }
                                Item { Layout.fillWidth: true }
                                Label {
                                    text: {
                                        var row = root.selectedRow()
                                        if (!row) return ""
                                        if (root.selectedKind === "task") return "并发数 " + row.batchSize
                                        if (root.selectedKind === "favorite") return "收藏 " + root.stamp(row.favorited_at)
                                        if (root.selectedKind === "active") return row.status === "running" ? "转录中" : "等待中"
                                        return root.recordTime(row)
                                    }
                                    font.family: root.mono
                                    font.pixelSize: 10
                                    color: root.faint
                                }
                            }
                        }

                        // 任务字段（可编辑）
                        ColumnLayout {
                            visible: root.selectedKind === "task" && root.selectedRow() !== null
                            Layout.fillWidth: true
                            Layout.leftMargin: 20
                            Layout.rightMargin: 20
                            Layout.topMargin: 15
                            spacing: 11
                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: 3
                                FieldLabel { text: "输入 Input" }
                                Label { Layout.fillWidth: true; text: root.selectedRow() ? root.pathOf(root.selectedRow()) : ""; wrapMode: Text.WrapAnywhere; font.pixelSize: 11; color: "#333333" }
                            }
                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: 3
                                FieldLabel { text: "输出目录" }
                                RowLayout {
                                    Layout.fillWidth: true
                                    spacing: 6
                                    Label {
                                        Layout.fillWidth: true
                                        text: root.selectedRow() ? (root.selectedRow().outputDirectoryRule === "source_directory" ? root.directoryOf(root.selectedRow()) : root.selectedRow().explicitOutputDirectory || "") : ""
                                        elide: Text.ElideMiddle
                                        font.family: root.mono
                                        font.pixelSize: 11
                                        color: "#333333"
                                    }
                                    PencilButton {
                                        tip: "修改输出目录"
                                        onClicked: outputMenu.open()
                                        Menu {
                                            id: outputMenu
                                            MenuItem { text: "跟随源目录"; onTriggered: root.editWork({outputDirectoryRule: "source_directory", explicitOutputDirectory: null}) }
                                            MenuItem { text: "选择目录"; onTriggered: { root.outputPickerTarget = "task"; facade.pickOutputDirectory() } }
                                        }
                                    }
                                }
                            }
                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: 3
                                FieldLabel { text: "MIDI 文件名" }
                                RowLayout {
                                    Layout.fillWidth: true
                                    spacing: 6
                                    Label {
                                        Layout.fillWidth: true
                                        text: root.selectedRow() && root.selectedRow().midiFileName !== undefined ? root.selectedRow().midiFileName : ""
                                        elide: Text.ElideMiddle
                                        font.family: root.mono
                                        font.pixelSize: 11
                                        color: "#333333"
                                    }
                                    PencilButton { tip: "修改 MIDI 文件名"; onClicked: { midiNameField.text = root.selectedRow().midiFileName; midiNameDialog.open() } }
                                }
                            }
                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: 3
                                FieldLabel { text: "并发数" }
                                RowLayout {
                                    Layout.fillWidth: true
                                    spacing: 6
                                    Label {
                                        Layout.fillWidth: true
                                        text: root.selectedRow() ? String(root.selectedRow().batchSize) : ""
                                        font.pixelSize: 11
                                        color: "#333333"
                                    }
                                    PencilButton { tip: "修改并发数"; onClicked: { batchSizeField.value = root.selectedRow().batchSize; batchSizeDialog.open() } }
                                }
                            }
                        }

                        // 收藏框（.favbox：历史已完成 / 收藏条目）
                        Rectangle {
                            visible: root.selectedRow() !== null
                                     && ((root.selectedKind === "history" && root.selectedRow().status === "succeeded")
                                         || root.selectedKind === "favorite")
                            Layout.fillWidth: true
                            Layout.preferredHeight: favBoxLayout.implicitHeight + 18
                            Layout.leftMargin: 20
                            Layout.rightMargin: 20
                            Layout.topMargin: 15
                            color: root.fill
                            border.color: root.rule
                            radius: 2
                            RowLayout {
                                id: favBoxLayout
                                anchors.fill: parent
                                anchors.margins: 9
                                spacing: 9
                                StarButton {
                                    visible: root.selectedKind === "history"
                                    text: root.selectedRow() && root.selectedRow().favorite_id ? "★" : "☆"
                                    on: root.selectedRow() && !!root.selectedRow().favorite_id
                                    Layout.alignment: Qt.AlignVCenter
                                    onClicked: {
                                        if (root.selectedRow().favorite_id) facade.command("removeFavorites", {ids: [root.selectedRow().favorite_id]})
                                        else facade.command("addFavorite", {attemptId: root.selectedId})
                                    }
                                }
                                ColumnLayout {
                                    Layout.fillWidth: true
                                    spacing: 3
                                    Label {
                                        text: root.selectedKind === "favorite" ? "所属收藏夹"
                                            : (root.selectedRow() && root.selectedRow().favorite_id ? "已收藏" : "未收藏")
                                        font.family: "Microsoft YaHei UI"
                                        font.pixelSize: 11
                                        font.weight: Font.Normal
                                        color: "#333333"
                                    }
                                    Label {
                                        Layout.fillWidth: true
                                        text: {
                                            if (root.selectedKind === "favorite") {
                                                var ids = root.selectedRow() ? (root.selectedRow().collectionIds || []) : []
                                                var names = ids.map(function(cid) {
                                                    var found = root.data.collections.find(function(c) { return c.collection_id === cid })
                                                    return found ? found.name : ""
                                                }).filter(function(n) { return n.length > 0 })
                                                return names.length ? names.join("  ") : "未分类"
                                            }
                                            var fav = root.selectedRow() && root.selectedRow().favorite_id
                                            if (!fav) return "点星把这条收进收藏夹"
                                            var row = root.data.favorites.items.find(function(f) { return f.favorite_id === root.selectedRow().favorite_id })
                                            var favIds = row ? (row.collectionIds || []) : []
                                            var favNames = favIds.map(function(cid) {
                                                var found = root.data.collections.find(function(c) { return c.collection_id === cid })
                                                return found ? found.name : ""
                                            }).filter(function(n) { return n.length > 0 })
                                            return favNames.length ? favNames.join("  ") : "未分类"
                                        }
                                        font.pixelSize: 10
                                        color: root.faint
                                        elide: Text.ElideRight
                                    }
                                }
                                WorkButton {
                                    small: true
                                    text: root.selectedKind === "favorite" ? "分组…" : "加入收藏夹…"
                                    enabled: root.selectedKind === "favorite" || !!(root.selectedRow() && root.selectedRow().favorite_id)
                                    visible: root.data.collections.length > 0 || root.selectedKind === "favorite"
                                    onClicked: {
                                        if (root.selectedKind === "favorite") root.selectedFavoriteIds = [root.selectedId]
                                        else root.selectedFavoriteIds = [root.selectedRow().favorite_id]
                                        bulkCollectionDialog.open()
                                    }
                                }
                            }
                        }

                        // 失败警告（.alert）
                        Rectangle {
                            visible: root.selectedKind === "history" && root.selectedRow() !== null && root.selectedRow().status === "failed"
                            Layout.fillWidth: true
                            Layout.preferredHeight: alertBox.implicitHeight + 18
                            Layout.leftMargin: 20
                            Layout.rightMargin: 20
                            Layout.topMargin: 12
                            color: "#fdf4f4"
                            border.color: "#c9a3a3"
                            radius: 2
                            ColumnLayout {
                                id: alertBox
                                anchors.fill: parent
                                anchors.margins: 9
                                spacing: 3
                                Label {
                                    text: root.selectedRow() ? (root.selectedRow().error_code || "转录失败") : ""
                                    font.family: "Microsoft YaHei UI"
                                    font.pixelSize: 12
                                    font.weight: Font.Normal
                                    color: root.bad
                                }
                                Label {
                                    Layout.fillWidth: true
                                    text: root.selectedRow() ? (root.selectedRow().error_message || "") : ""
                                    wrapMode: Text.WrapAnywhere
                                    font.pixelSize: 11
                                    color: "#5a4040"
                                }
                            }
                        }

                        Rectangle {
                            visible: root.selectedRow() !== null
                            Layout.fillWidth: true
                            Layout.preferredHeight: 1
                            Layout.leftMargin: 20
                            Layout.rightMargin: 20
                            Layout.topMargin: 16
                            Layout.bottomMargin: 13
                            color: root.rule
                        }

                        // 任务操作
                        ColumnLayout {
                            visible: root.selectedKind === "task" && root.selectedRow() !== null
                            Layout.fillWidth: true
                            Layout.leftMargin: 20
                            Layout.rightMargin: 20
                            Layout.topMargin: 7
                            spacing: 7
                            WorkButton { Layout.fillWidth: true; text: "试听原音频"; onClicked: root.play("task", root.selectedId, "audio") }
                            WorkButton { Layout.fillWidth: true; text: "复制输入路径"; onClicked: { facade.copyText(root.selectedRow().inputPath); root.statusText = "输入路径已复制" } }
                            Label {
                                Layout.fillWidth: true
                                text: "输出目录、MIDI 文件名和并发数在上方字段右侧的铅笔处修改。这里的修改只影响这一项；顶部的「批量配置」才会同时改动多个已勾选项。"
                                wrapMode: Text.Wrap
                                font.pixelSize: 10
                                color: root.faint
                                topPadding: 4
                                lineHeight: 1.5
                            }
                        }

                        // 执行中操作
                        ColumnLayout {
                            visible: root.selectedKind === "active" && root.selectedRow() !== null
                            Layout.fillWidth: true
                            Layout.leftMargin: 20
                            Layout.rightMargin: 20
                            Layout.topMargin: 7
                            spacing: 7
                            WorkButton {
                                Layout.fillWidth: true
                                text: root.selectedRow() && root.selectedRow().status === "running" ? "取消当前转录" : "取消此等待任务"
                                onClicked: facade.command("cancelAttempt", {attemptId: root.selectedId})
                            }
                            Label {
                                Layout.fillWidth: true
                                text: root.selectedRow() && root.selectedRow().status === "running"
                                      ? "引擎不转发阶段事件，因此这里只能显示「转录中」，不显示百分比或剩余时间。"
                                      : "排队中的任务会立即取消。已提交的配置只读。"
                                wrapMode: Text.Wrap
                                font.pixelSize: 10
                                color: root.faint
                                topPadding: 4
                                lineHeight: 1.5
                            }
                        }

                        // 历史/收藏操作
                        ColumnLayout {
                            visible: (root.selectedKind === "history" || root.selectedKind === "favorite") && root.selectedRow() !== null
                            Layout.fillWidth: true
                            Layout.leftMargin: 20
                            Layout.rightMargin: 20
                            Layout.topMargin: 7
                            spacing: 7
                            WorkButton {
                                Layout.fillWidth: true
                                visible: root.selectedKind === "history" && root.selectedRow() !== null && root.selectedRow().status === "failed"
                                text: "重新提交"
                                primary: true
                                onClicked: facade.command("previewRequeue", {ids: [root.selectedId]})
                            }
                            WorkButton {
                                Layout.fillWidth: true
                                visible: root.selectedKind === "history" && root.selectedRow() !== null && root.selectedRow().status === "cancelled"
                                text: "重新提交"
                                primary: true
                                onClicked: facade.command("previewRequeue", {ids: [root.selectedId]})
                            }
                            WorkButton {
                                Layout.fillWidth: true
                                visible: root.selectedKind === "history" && root.selectedRow() !== null && root.selectedRow().status === "failed"
                                text: "复制错误详情"
                                onClicked: { facade.copyText(root.selectedRow().error_message || ""); root.statusText = "错误详情已复制" }
                            }
                            WorkButton {
                                Layout.fillWidth: true
                                visible: root.selectedKind === "favorite" || (root.selectedKind === "history" && root.selectedRow() !== null && root.selectedRow().status === "succeeded")
                                text: "载入联播 · 检查结果"
                                primary: true
                                onClicked: root.play(root.selectedKind, root.selectedId, "paired")
                            }
                            WorkButton {
                                Layout.fillWidth: true
                                visible: root.selectedKind === "favorite" || (root.selectedKind === "history" && root.selectedRow() !== null && root.selectedRow().status === "succeeded")
                                text: "仅试听原音频"
                                onClicked: root.play(root.selectedKind, root.selectedId, "audio")
                            }
                            WorkButton {
                                Layout.fillWidth: true
                                visible: root.selectedKind === "favorite" || (root.selectedKind === "history" && root.selectedRow() !== null && root.selectedRow().status === "succeeded")
                                text: "仅试听 MIDI"
                                onClicked: root.play(root.selectedKind, root.selectedId, "midi")
                            }
                            Rectangle { visible: root.selectedKind === "favorite" || (root.selectedKind === "history" && root.selectedRow() !== null && root.selectedRow().status === "succeeded"); Layout.fillWidth: true; Layout.preferredHeight: 1; color: root.rule; Layout.topMargin: 8; Layout.bottomMargin: 2 }
                            WorkButton {
                                Layout.fillWidth: true
                                visible: root.selectedKind === "favorite" || (root.selectedKind === "history" && root.selectedRow() !== null && root.selectedRow().status === "succeeded")
                                text: "打开 MIDI 所在目录"
                                onClicked: facade.openContainingFolder(root.selectedRow().midi_path)
                            }
                            WorkButton {
                                Layout.fillWidth: true
                                visible: root.selectedKind === "favorite" || (root.selectedKind === "history" && root.selectedRow() !== null && root.selectedRow().status === "succeeded")
                                text: "复制 MIDI 路径"
                                onClicked: { facade.copyText(root.selectedRow().midi_path); root.statusText = "MIDI 路径已复制" }
                            }
                            WorkButton {
                                Layout.fillWidth: true
                                visible: root.selectedKind === "history" && root.selectedRow() !== null && (root.selectedRow().status === "failed" || root.selectedRow().status === "cancelled")
                                text: "打开输入所在目录"
                                onClicked: facade.openContainingFolder(root.selectedRow().input_path)
                            }
                            WorkButton {
                                Layout.fillWidth: true
                                visible: root.selectedKind === "favorite"
                                text: "移出收藏"
                                onClicked: facade.command("removeFavorites", {ids: [root.selectedId]})
                            }
                            WorkButton {
                                Layout.fillWidth: true
                                visible: root.selectedKind === "history"
                                text: "删除历史"
                                onClicked: facade.command("previewDeleteHistory", {ids: [root.selectedId]})
                            }
                            Label {
                                Layout.fillWidth: true
                                text: root.selectedKind === "favorite"
                                      ? "移出收藏只删除这份收藏，不影响历史记录，也不删除任何文件。原音频和 MIDI 都还在磁盘上。"
                                      : "历史只保存元数据，删除记录不删除音频或 MIDI 文件，也不影响收藏夹里的那一份。"
                                wrapMode: Text.Wrap
                                font.pixelSize: 10
                                color: root.faint
                                topPadding: 4
                                lineHeight: 1.5
                            }
                        }
                        Item { Layout.fillHeight: true; Layout.preferredHeight: 20 }
                    }
                }
            }
        }

        // ── 播放器（.player，钢琴常驻）────────────────────────────────
        Rectangle {
            id: player
            anchors.bottom: parent.bottom
            anchors.left: parent.left
            anchors.right: parent.right
            height: slot.height + 64
            color: root.shell
            Rectangle { anchors.top: parent.top; width: parent.width; height: 1; color: root.strongRule }
            Column {
                anchors.fill: parent
                anchors.leftMargin: 20
                anchors.rightMargin: 20
                anchors.topMargin: 10
                anchors.bottomMargin: 12
                spacing: 8
                Row {
                    width: parent.width
                    height: 34
                    spacing: 16
                    Row {
                        spacing: 6
                        anchors.verticalCenter: parent.verticalCenter
                        WorkButton { text: "播放"; small: true; enabled: !!root.player.sessionId && root.player.state !== "playing"; onClicked: facade.togglePlayback() }
                        WorkButton { text: "暂停"; small: true; enabled: !!root.player.sessionId && root.player.state === "playing"; onClicked: facade.togglePlayback() }
                        WorkButton { text: "终止"; small: true; enabled: !!root.player.sessionId; onClicked: facade.stopPlayback() }
                    }
                    Column {
                        width: parent.width - 260
                        anchors.verticalCenter: parent.verticalCenter
                        spacing: 7
                        PlaybackLane { track: "audio"; width: parent.width }
                        PlaybackLane { track: "midi"; width: parent.width }
                    }
                }
                Rectangle {
                    id: slot
                    x: (parent.width - width) / 2
                    width: Math.min(parent.width, 1400)
                    height: Math.round(keyboard.width / 52 * 5.8) + 16
                    radius: 3
                    color: root.well
                    border.color: root.strongRule
                    Item {
                        id: keyboard
                        anchors.top: parent.top
                        anchors.bottom: parent.bottom
                        anchors.horizontalCenter: parent.horizontalCenter
                        anchors.topMargin: 7
                        anchors.bottomMargin: 9
                        width: parent.width - 16
                        property real keyWidth: width / 52
                        Repeater {
                            model: root.whiteNotes
                            delegate: Rectangle {
                                required property int index; required property int modelData
                                x: index * keyboard.keyWidth
                                width: keyboard.keyWidth + 0.5
                                height: keyboard.height
                                gradient: Gradient {
                                    GradientStop { position: 0; color: root.notes[modelData] !== undefined ? "#F0514E" : "#ffffff" }
                                    GradientStop { position: 1; color: root.notes[modelData] !== undefined ? "#F0514E" : "#f2f2f2" }
                                }
                                border.color: root.notes[modelData] !== undefined ? "#5f5f5f" : "#8f8f8f"
                            }
                        }
                        Repeater {
                            model: root.blackNotes
                            delegate: Rectangle {
                                required property int modelData
                                x: root.whitesBefore(modelData) * keyboard.keyWidth - width / 2
                                width: keyboard.keyWidth * 0.6
                                height: keyboard.height * 0.58
                                radius: 2
                                z: 2
                                gradient: Gradient {
                                    GradientStop { position: 0; color: root.notes[modelData] !== undefined ? "#C30E0E" : "#3a3a3a" }
                                    GradientStop { position: 1; color: root.notes[modelData] !== undefined ? "#C30E0E" : "#1a1a1a" }
                                }
                                border.color: root.notes[modelData] !== undefined ? "#7d0808" : "#111111"
                            }
                        }
                    }
                }
            }
        }

        // 提示条（.toast，可带动作按钮）
        Rectangle {
            z: 80
            anchors.horizontalCenter: parent.horizontalCenter
            anchors.bottom: parent.bottom
            anchors.bottomMargin: 190
            width: Math.min(parent.width - 40, toastRow.implicitWidth + 34)
            height: 38
            radius: 2
            color: root.accent
            visible: root.statusText.length > 0 || root.player.state === "preparing"
            Row {
                id: toastRow
                anchors.centerIn: parent
                spacing: 12
                Label {
                    text: root.player.state === "preparing"
                        ? (root.player.mode === "midi" || root.player.mode === "paired" ? "正在载入 MIDI 音色…" : "正在准备播放…")
                        : root.statusText
                    color: "white"
                    font.pixelSize: 12
                    elide: Text.ElideRight
                    anchors.verticalCenter: parent.verticalCenter
                }
                Button {
                    id: toastActionButton
                    visible: root.player.state !== "preparing" && root.toastActionText.length > 0
                    text: root.toastActionText
                    height: 24
                    padding: 0
                    leftPadding: 9; rightPadding: 9
                    font.pixelSize: 11
                    contentItem: Text { text: toastActionButton.text; color: "white"; font.pixelSize: 11; anchors.verticalCenter: parent.verticalCenter }
                    background: Rectangle { radius: 2; color: toastActionButton.down ? "#33ffffff" : "transparent"; border.color: "#66ffffff" }
                    anchors.verticalCenter: parent.verticalCenter
                    onClicked: {
                        if (root.toastFavoriteId) {
                            root.selectedFavoriteIds = [root.toastFavoriteId]
                            bulkCollectionDialog.open()
                        }
                        root.toastActionText = ""
                        root.toastFavoriteId = ""
                    }
                }
            }
        }
        Timer { id: statusTimer; interval: 3500; onTriggered: { root.statusText = ""; root.toastActionText = ""; root.toastFavoriteId = "" } }
    }

    // ── 弹窗 ─────────────────────────────────────────────────────────
    component ModalBackground: Rectangle { color: root.paper; border.color: root.strongRule; radius: 3 }
    component ModalTag: RowLayout {
        id: tag
        property string section: ""
        property string label: ""
        spacing: 5
        Text { text: tag.section; color: root.faint; font.family: root.mono; font.pixelSize: 10; font.bold: true; font.letterSpacing: 1.1 }
        Text { text: "· " + tag.label; color: root.faint; font.family: "Microsoft YaHei UI"; font.pixelSize: 10; font.weight: Font.Normal; renderType: Text.NativeRendering }
    }
    component ModalTitle: Text {
        color: root.ink
        font.family: "Microsoft YaHei UI"
        font.pixelSize: 17
        font.weight: Font.Normal
        renderType: Text.NativeRendering
    }
    component ModalActions: Item {
        id: actions
        property var dialog
        property string dismissText: "取消"
        property string confirmText: ""
        implicitHeight: 68
        RowLayout {
            anchors.fill: parent
            anchors.leftMargin: 24
            anchors.rightMargin: 24
            anchors.bottomMargin: 16
            spacing: 8
            Item { Layout.fillWidth: true }
            WorkButton { text: actions.dismissText; font.family: "Microsoft YaHei UI"; onClicked: actions.dialog.reject() }
            WorkButton {
                visible: !!actions.confirmText
                text: actions.confirmText
                font.family: "Microsoft YaHei UI"
                primary: true
                onClicked: actions.dialog.accept()
            }
        }
    }
    Dialog {
        id: settingsDialog; modal: true; anchors.centerIn: parent
        width: 600; height: Math.min(root.height - 40, 570)
        standardButtons: Dialog.NoButton
        background: ModalBackground {}
        contentItem: ColumnLayout {
            spacing: 0
            ScrollView {
                id: settingsScroll
                Layout.fillWidth: true
                Layout.fillHeight: true
                leftPadding: 20
                rightPadding: 20
                ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
                ColumnLayout {
                    width: settingsScroll.availableWidth
                    spacing: 10
                    Item { Layout.preferredHeight: 10 }
                    ModalTag { section: "SETTINGS"; label: "应用设置" }
                    ModalTitle { text: "Better PianoTrans 设置" }
                    FieldLabel { text: "界面主题"; Layout.topMargin: 8 }
                    Label {
                        Layout.fillWidth: true; wrapMode: Text.Wrap
                        text: "当前：第一版工作台。第二套视觉方案接入后，可在这里切换。"
                        color: root.muted; font.pixelSize: 12
                    }
                    Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: root.rule; Layout.topMargin: 6 }
                    FieldLabel { text: "转录设备"; Layout.topMargin: 6 }
                    Label { Layout.fillWidth: true; wrapMode: Text.Wrap; text: "自动检测显卡；仅 GPU 不可用时会报错。已提交的任务保持原设备模式。"; color: root.muted; font.pixelSize: 12 }
                    RowLayout {
                        spacing: 6
                        WorkButton { text: "自动"; primary: root.data.deviceMode === "auto"; onClicked: facade.command("setDevice", {mode: "auto"}) }
                        WorkButton { text: "仅 GPU"; primary: root.data.deviceMode === "cuda"; onClicked: facade.command("setDevice", {mode: "cuda"}) }
                        WorkButton { text: "仅 CPU"; primary: root.data.deviceMode === "cpu"; onClicked: facade.command("setDevice", {mode: "cpu"}) }
                    }
                    Label { text: "当前检测结果：" + (root.data.detectedDevice === "cuda" ? "CUDA" : "CPU"); font.pixelSize: 11; color: root.faint }
                    Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: root.rule; Layout.topMargin: 6 }
                    FieldLabel { text: "数据库"; Layout.topMargin: 6 }
                    Label {
                        Layout.fillWidth: true; wrapMode: Text.WrapAnywhere
                        text: root.data.databasePath || "正在读取…"
                        font.family: root.mono; font.pixelSize: 11; color: root.ink
                    }
                    WorkButton {
                        text: "打开数据库所在文件夹"; small: true
                        enabled: !!root.data.databasePath
                        onClicked: facade.openContainingFolder(root.data.databasePath)
                    }
                    Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: root.rule; Layout.topMargin: 6 }
                    FieldLabel { text: "关于与致谢"; Layout.topMargin: 6 }
                    Label { text: "版本：" + (root.data.appVersion || "开发版"); font.pixelSize: 12; color: root.ink }
                    Label { Layout.fillWidth: true; wrapMode: Text.Wrap; text: "应用源码采用 GPL-3.0-only；第三方组件保留各自许可。"; color: root.muted; font.pixelSize: 12 }
                    Label {
                        Layout.fillWidth: true; wrapMode: Text.Wrap
                        text: "钢琴转录核心来源于字节跳动的 Piano Transcription 项目。感谢孔秋强（Qiuqiang Kong）及原研究团队。Better PianoTrans 在此基础上开发，并修改了部分推理与 MIDI 输出代码。"
                        color: root.ink; font.pixelSize: 12
                    }
                    Label { Layout.fillWidth: true; wrapMode: Text.Wrap; text: "完整论文作者名单、上游来源和修改记录见第三方来源说明。"; color: root.muted; font.pixelSize: 11 }
                    RowLayout {
                        spacing: 6
                        WorkButton { text: "查看 GPL 许可"; small: true; onClicked: facade.openLegalFile("license") }
                        WorkButton { text: "查看模型来源说明"; small: true; onClicked: facade.openLegalFile("credits") }
                        WorkButton { text: "第三方许可目录"; small: true; onClicked: facade.openLegalFile("third_party") }
                    }
                    Item { Layout.preferredHeight: 10 }
                }
            }
            RowLayout {
                Layout.fillWidth: true
                Layout.margins: 10
                WorkButton { text: "关闭软件"; onClicked: { settingsDialog.close(); root.close() } }
                Item { Layout.fillWidth: true }
                WorkButton { text: "完成"; onClicked: settingsDialog.close() }
            }
        }
    }
    Dialog {
        id: conflictDialog; modal: true; anchors.centerIn: parent; width: 560; standardButtons: Dialog.NoButton
        property var conflict: root.submitted.filter(function(item) { return item.status === "conflict" })[0] || null
        property bool previewActive: !!(conflict && root.player.source && root.player.source.kind === "conflict"
                                        && root.player.source.id === conflict.taskId)
        background: ModalBackground {}
        onOpened: applyRemaining.checked = false
        onRejected: {
            if (root.submissionId) facade.command("abandonSubmission", {submissionId: root.submissionId})
            root.submissionId = ""
        }
        contentItem: ColumnLayout {
            spacing: 10
            ModalTag { section: "CONFLICT"; label: "输出冲突" }
            ModalTitle { text: "目标 MIDI 已存在" }
            FieldLabel { text: "输入" }
            ValBox { Layout.fillWidth: true; text: conflictDialog.conflict ? conflictDialog.conflict.inputPath : "" }
            FieldLabel { text: "输出" }
            ValBox { Layout.fillWidth: true; text: conflictDialog.conflict ? conflictDialog.conflict.midiPath : "" }
            Label {
                Layout.fillWidth: true; wrapMode: Text.Wrap
                text: conflictDialog.conflict && conflictDialog.conflict.conflictOrigin
                    ? "发现同名 MIDI。它与「" + conflictDialog.conflict.conflictOrigin.inputFileName + "」的登记结果内容一致。"
                    : "发现同名 MIDI，无法确认唯一来源。你可以先试听，再决定如何处理。"
                color: "#3d3d3d"; font.pixelSize: 12
            }
            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: 40
                visible: !!(conflictDialog.conflict && !conflictDialog.conflict.previewError)
                color: root.fill
                border.color: root.rule
                radius: 2
                RowLayout {
                    anchors.fill: parent
                    anchors.margins: 7
                    spacing: 10
                    WorkButton {
                        small: true
                        text: conflictDialog.previewActive && root.player.state === "playing" ? "暂停" : "试听"
                        enabled: conflictDialog.conflict && !conflictDialog.conflict.previewError
                        onClicked: {
                            if (root.player.source && root.player.source.kind === "conflict" && root.player.source.id === conflictDialog.conflict.taskId)
                                facade.togglePlayback()
                            else facade.command("previewConflictMidi", {submissionId: root.submissionId, taskId: conflictDialog.conflict.taskId})
                        }
                    }
                    Rectangle {
                        Layout.fillWidth: true
                        Layout.preferredHeight: 8
                        color: root.paper
                        border.color: root.strongRule
                        Rectangle {
                            width: parent.width * (conflictDialog.previewActive
                                ? Math.min(1, (root.player.positionSeconds || 0) / Math.max(1, root.player.durations.midi || 0)) : 0)
                            height: parent.height
                            color: root.accent
                        }
                    }
                    Label {
                        text: conflictDialog.previewActive ? root.shortTime(root.player.positionSeconds) + " / " + root.shortTime(root.player.durations.midi) : "00:00 / --:--"
                        font.pixelSize: 11; font.family: root.mono; color: root.muted
                    }
                }
            }
            Label { visible: conflictDialog.conflict && !!conflictDialog.conflict.previewError; text: conflictDialog.conflict ? conflictDialog.conflict.previewError || "" : ""; color: root.bad; font.pixelSize: 11; wrapMode: Text.Wrap }
            Label { visible: !!root.conflictError; Layout.fillWidth: true; text: root.conflictError; color: root.bad; font.pixelSize: 11; wrapMode: Text.Wrap }
            Label { text: conflictDialog.conflict ? "请选择本项的输出方式。" : ""; color: root.muted; font.pixelSize: 12 }
            CheckBox { id: applyRemaining; text: "对本次提交的后续冲突使用相同选择"; font.pixelSize: 12 }
            RowLayout {
                Layout.fillWidth: true
                spacing: 8
                WorkButton { text: "取消本项"; onClicked: { facade.command("decideConflict", {submissionId: root.submissionId, taskId: conflictDialog.conflict.taskId, choice: "skip", applyToRemaining: applyRemaining.checked}); conflictDialog.close() } }
                WorkButton { text: "另存编号"; onClicked: { facade.command("decideConflict", {submissionId: root.submissionId, taskId: conflictDialog.conflict.taskId, choice: "rename", applyToRemaining: applyRemaining.checked}); conflictDialog.close() } }
                Item { Layout.fillWidth: true }
                WorkButton {
                    text: "覆盖现有 MIDI"
                    primary: true
                    enabled: !!conflictDialog.conflict
                    ToolTip.visible: hovered; ToolTip.text: "若目标 MIDI 在转录期间变化，会停止覆盖"
                    onClicked: {
                        facade.command("decideConflict", {submissionId: root.submissionId, taskId: conflictDialog.conflict.taskId,
                                                          choice: "overwrite", applyToRemaining: applyRemaining.checked})
                        conflictDialog.close()
                    }
                }
            }
        }
    }
    Dialog {
        id: batchDialog; modal: true; anchors.centerIn: parent; width: 500; standardButtons: Dialog.NoButton
        background: ModalBackground {}
        contentItem: ColumnLayout {
            spacing: 10
            ModalTag { section: "BULK CONFIG"; label: "批量配置" }
            ModalTitle { text: "批量配置 · " + root.data.work.checkedCount + " 项" }
            Label { Layout.fillWidth: true; wrapMode: Text.Wrap; text: "只作用于已勾选的 " + root.data.work.checkedCount + " 项。正在转录和已结束的任务不受影响。单项差异可以在右侧检查器逐项修改。"; color: "#3d3d3d"; font.pixelSize: 12 }
            FieldLabel { text: "输出目录"; Layout.topMargin: 8 }
            RowLayout {
                spacing: 6
                WorkButton { text: "跟随各自源目录"; primary: root.batchSource; onClicked: root.batchSource = true }
                WorkButton { text: "统一到同一目录"; primary: !root.batchSource; onClicked: root.batchSource = false }
            }
            RowLayout {
                Layout.fillWidth: true
                visible: !root.batchSource
                spacing: 6
                TextField {
                    id: bulkDirField
                    Layout.fillWidth: true
                    Layout.preferredHeight: 30
                    placeholderText: "例如 D:\\MIDI"
                    placeholderTextColor: root.faint
                    color: root.ink
                    selectionColor: root.sel
                    selectedTextColor: root.ink
                    font.family: "Microsoft YaHei UI"
                    font.pixelSize: 11
                    text: root.batchDirectory
                    onTextEdited: root.batchDirectory = text
                    background: Rectangle { radius: 2; border.color: root.strongRule; color: root.fill }
                }
                WorkButton { text: "浏览…"; small: true; onClicked: { root.outputPickerTarget = "batch"; facade.pickOutputDirectory() } }
            }
            Label { visible: root.batchSource; text: "每个 MIDI 写回它自己的音频目录。"; font.pixelSize: 10; color: root.faint }
            Label { visible: !root.batchSource; text: "所有 MIDI 写入这一个目录，同名冲突仍会逐项询问。"; font.pixelSize: 10; color: root.faint }
            RowLayout { Layout.fillWidth: true; Item { Layout.fillWidth: true }
                WorkButton { text: "应用输出目录"; small: true; enabled: root.batchSource || !!root.batchDirectory; onClicked: root.batchApply(true, false) } }
            Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: root.rule }
            FieldLabel { text: "并发数" }
            RowLayout {
                spacing: 6
                WorkButton { text: "4"; primary: root.batchSize === 4; onClicked: root.batchSize = 4 }
                WorkButton { text: "16"; primary: root.batchSize === 16; onClicked: root.batchSize = 16 }
                SpinBox { from: 1; to: 256; value: root.batchSize; onValueModified: root.batchSize = value }
            }
            Label { text: "显存不足时不会自动降低并发数，只会报错。"; font.pixelSize: 10; color: root.faint }
            RowLayout {
                Layout.fillWidth: true
                spacing: 8
                WorkButton { text: "取消"; onClicked: batchDialog.close() }
                WorkButton { text: "应用并发数"; small: true; onClicked: root.batchApply(false, true) }
                Item { Layout.fillWidth: true }
                WorkButton { text: "全部应用"; primary: true; enabled: root.batchSource || !!root.batchDirectory; onClicked: { root.batchApply(true, true); batchDialog.close() } }
            }
        }
    }
    Dialog {
        id: midiNameDialog; modal: true; anchors.centerIn: parent; width: 400
        standardButtons: Dialog.NoButton
        background: ModalBackground {}
        footer: ModalActions { dialog: midiNameDialog; confirmText: "保存" }
        contentItem: ColumnLayout {
            spacing: 10
            ModalTag { section: "PER TASK"; label: "单项改名" }
            ModalTitle { text: "修改 MIDI 文件名" }
            FieldLabel { text: "MIDI 文件名" }
            TextField {
                id: midiNameField
                Layout.fillWidth: true
                Layout.preferredHeight: 30
                selectByMouse: true
                color: root.ink
                selectionColor: root.sel
                selectedTextColor: root.ink
                font.family: "Microsoft YaHei UI"
                font.pixelSize: 11
                background: Rectangle { radius: 2; border.color: root.strongRule; color: root.fill }
            }
            Label { text: "不需要和音频同名。后缀 .mid 会自动补全。"; font.pixelSize: 10; color: root.faint }
        }
        onAccepted: root.editWork({midiFileName: midiNameField.text})
    }
    Dialog {
        id: batchSizeDialog; modal: true; anchors.centerIn: parent; width: 300
        standardButtons: Dialog.NoButton
        background: ModalBackground {}
        footer: ModalActions { dialog: batchSizeDialog; confirmText: "保存" }
        contentItem: ColumnLayout {
            spacing: 10
            ModalTag { section: "PER TASK"; label: "单项并发" }
            ModalTitle { text: "修改并发数" }
            SpinBox { id: batchSizeField; from: 1; to: 256 }
            Label { text: "只影响这一项。默认 16；显存不足时不会自动降低，只会报错。"; wrapMode: Text.Wrap; font.pixelSize: 10; color: root.faint }
        }
        onAccepted: root.editWork({batchSize: batchSizeField.value})
    }
    Dialog {
        id: collectionDialog
        modal: true; anchors.centerIn: parent; width: 480
        padding: 24
        bottomPadding: 8
        standardButtons: Dialog.NoButton
        background: ModalBackground {}
        contentItem: ColumnLayout {
            spacing: 9
            ModalTag { section: "LIBRARY"; label: "收藏夹" }
            ModalTitle {
                text: root.editingCollectionId ? "重命名收藏夹" : "新建收藏夹"
                Layout.topMargin: 4
                Layout.bottomMargin: 8
            }
            Text { text: "名称"; color: root.faint; font.family: "Microsoft YaHei UI"; font.pixelSize: 11; font.weight: Font.Normal; renderType: Text.NativeRendering }
            TextField {
                id: collectionName
                Layout.fillWidth: true
                Layout.preferredHeight: 38
                placeholderText: "例如 练习用 / 肖邦 / 演出候选"
                placeholderTextColor: root.faint
                selectByMouse: true
                color: root.ink
                selectionColor: root.sel
                selectedTextColor: root.ink
                font.family: "Microsoft YaHei UI"
                font.pixelSize: 12
                leftPadding: 10
                rightPadding: 10
                background: Rectangle { radius: 2; border.color: collectionName.activeFocus ? root.ink : root.strongRule; color: root.fill }
                onAccepted: if (text.trim()) collectionDialog.accept()
            }
            Text {
                visible: !root.editingCollectionId
                Layout.fillWidth: true
                Layout.topMargin: 7
                text: "一个条目可以同时属于多个收藏夹。新建后可以再把条目分进去。"
                wrapMode: Text.Wrap
                color: root.muted
                font.family: "Microsoft YaHei UI"
                font.pixelSize: 11
                renderType: Text.NativeRendering
            }
        }
        footer: Item {
            implicitHeight: 68
            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: 24
                anchors.rightMargin: 24
                anchors.bottomMargin: 16
                spacing: 8
                Item { Layout.fillWidth: true }
                WorkButton { text: "取消"; font.family: "Microsoft YaHei UI"; onClicked: collectionDialog.reject() }
                WorkButton {
                    text: root.editingCollectionId ? "保存" : "创建"
                    font.family: "Microsoft YaHei UI"
                    primary: true
                    enabled: !!collectionName.text.trim()
                    onClicked: collectionDialog.accept()
                }
            }
        }
        onOpened: collectionName.forceActiveFocus()
        onAccepted: {
            if (root.editingCollectionId) facade.command("renameCollection", {id: root.editingCollectionId, name: collectionName.text, revision: root.editingCollectionRevision})
            else facade.command("createCollection", {name: collectionName.text})
            root.editingCollectionId = ""; collectionName.clear()
        }
    }
    Dialog {
        id: bulkCollectionDialog
        modal: true; anchors.centerIn: parent; width: 420
        standardButtons: Dialog.NoButton
        background: ModalBackground {}
        footer: ModalActions { dialog: bulkCollectionDialog; dismissText: "关闭" }
        contentItem: ColumnLayout {
            spacing: 12
            ModalTag { section: "LIBRARY"; label: "收藏夹" }
            ModalTitle { text: "加入收藏夹" }
            Label { text: "已选择 " + root.selectedFavoriteIds.length + " 项收藏"; color: root.muted; font.pixelSize: 12 }
            Repeater {
                id: bulkCollectionRepeater
                model: root.data.collections
                delegate: CheckBox {
                    required property var modelData
                    text: modelData.name
                    font.pixelSize: 12
                }
            }
            RowLayout {
                Layout.fillWidth: true
                spacing: 8
                WorkButton { text: "＋ 新建收藏夹"; small: true; onClicked: { root.editingCollectionId = ""; collectionName.clear(); collectionDialog.open() } }
                Item { Layout.fillWidth: true }
                WorkButton {
                    text: "加入所选分组"
                    primary: true
                    enabled: root.selectedFavoriteIds.length > 0 && root.data.collections.length > 0
                    onClicked: {
                        var chosen = []
                        for (var i = 0; i < root.data.collections.length; i++) {
                            var box = bulkCollectionRepeater.itemAt(i)
                            if (box && box.checked) chosen.push(root.data.collections[i].collection_id)
                        }
                        if (chosen.length) facade.command("setMembership", {ids: root.selectedFavoriteIds, add: chosen})
                        for (var j = 0; j < root.data.collections.length; j++) {
                            var reset = bulkCollectionRepeater.itemAt(j)
                            if (reset) reset.checked = false
                        }
                        root.statusText = "已更新收藏分组"
                        bulkCollectionDialog.close()
                    }
                }
            }
        }
    }
    Dialog {
        id: deleteDialog; modal: true; anchors.centerIn: parent; width: 480
        padding: 24; bottomPadding: 8; standardButtons: Dialog.NoButton
        background: ModalBackground {}
        contentItem: ColumnLayout {
            spacing: 12
            ModalTag { section: "HISTORY"; label: "历史记录" }
            ModalTitle {
                text: "删除 " + (root.fileCheck.count || 0) + " 条记录？"
            }
            Text {
                Layout.fillWidth: true; Layout.topMargin: 4
                wrapMode: Text.Wrap
                text: "只从历史记录中移除这些条目。音频和 MIDI 文件不会被删除，收藏夹里的那一份会保留。"
                color: root.muted; font.family: "Microsoft YaHei UI"; font.pixelSize: 11
                renderType: Text.NativeRendering
            }
        }
        footer: Item {
            implicitHeight: 68
            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: 24
                anchors.rightMargin: 24
                anchors.bottomMargin: 16
                spacing: 8
                Item { Layout.fillWidth: true }
                WorkButton { text: "取消"; font.family: "Microsoft YaHei UI"; onClicked: deleteDialog.reject() }
                WorkButton { text: "删除记录"; font.family: "Microsoft YaHei UI"; primary: true; onClicked: deleteDialog.accept() }
            }
        }
        onAccepted: facade.command("confirmDeleteHistory", {token: root.fileCheck.token, ids: root.fileCheck.ids})
    }
    Dialog {
        id: requeueDialog; modal: true; anchors.centerIn: parent; width: 480; standardButtons: Dialog.NoButton
        background: ModalBackground {}
        footer: ModalActions { dialog: requeueDialog; confirmText: "重新提交" }
        contentItem: ColumnLayout {
            spacing: 10
            ModalTag { section: "REQUEUE"; label: "重新提交" }
            ModalTitle { text: "重新提交" }
            Label { Layout.fillWidth: true; wrapMode: Text.Wrap; text: "可重提 " + (root.fileCheck.eligible || []).length + " 项。已完成的无法重新提交：同一音频用同一模型转录，输出 MIDI 是字节级相同的，重跑没有意义。"; color: "#3d3d3d"; font.pixelSize: 12 }
        }
        onAccepted: facade.command("requeueEligible", {token: root.fileCheck.token, ids: root.fileCheck.eligible})
    }
    Dialog {
        id: fileDialog; modal: true; anchors.centerIn: parent; width: 460; standardButtons: Dialog.NoButton
        background: ModalBackground {}
        footer: ModalActions { dialog: fileDialog; dismissText: "关闭" }
        contentItem: ColumnLayout {
            spacing: 10
            ModalTag { section: "PLAYBACK"; label: "文件不在原位置" }
            ModalTitle { text: "文件缺失或与登记指纹不符" }
            Label { Layout.fillWidth: true; wrapMode: Text.WrapAnywhere; text: "按缺失组合提供可行的操作。搬家是常态，先试试在新位置找回来。"; color: "#3d3d3d"; font.pixelSize: 12 }
            WorkButton {
                text: "文件在新位置？"
                visible: (root.fileCheck.allowedActions || []).indexOf("relocate") >= 0 || (root.fileCheck.allowedActions || []).indexOf("reassign") >= 0
                onClicked: {
                    var checks = root.fileCheck.checks || {}
                    var kind = checks.midi === "missing" || checks.midi === "mismatch" ? "midi" : "audio"
                    facade.pickReplacement(root.playingEntry.kind, root.playingEntry.id, kind, root.fileCheck.revision)
                    fileDialog.close()
                }
            }
            WorkButton { text: "仅试听 MIDI"; visible: (root.fileCheck.allowedActions || []).indexOf("midi_only") >= 0; onClicked: { fileDialog.close(); root.play(root.playingEntry.kind, root.playingEntry.id, "midi") } }
            WorkButton {
                text: "核查并恢复结果"
                primary: true
                visible: (root.fileCheck.allowedActions || []).indexOf("recover_result") >= 0 && !!root.fileCheck.successRecordId
                onClicked: { fileDialog.close(); facade.command("inspectResult", {successRecordId: root.fileCheck.successRecordId}) }
            }
        }
    }
    Dialog {
        id: unverifiedDialog; modal: true; anchors.centerIn: parent; width: 460
        standardButtons: Dialog.NoButton
        background: ModalBackground {}
        footer: ModalActions { dialog: unverifiedDialog; dismissText: "取消" }
        contentItem: ColumnLayout {
            spacing: 10
            ModalTag { section: "PLAYBACK"; label: "无法确认原件" }
            ModalTitle { text: "无法确认文件是否为原件" }
            Label { Layout.fillWidth: true; wrapMode: Text.WrapAnywhere; text: "这条记录没有保存文件指纹，因此无法核对当前位置的文件是否与转录时相同。"; color: "#3d3d3d"; font.pixelSize: 12 }
            WorkButton { text: "仅本次播放"; primary: true; onClicked: { facade.confirmUnverifiedPlayback(root.unverifiedRequestId); unverifiedDialog.close() } }
        }
    }
    Dialog {
        id: duplicateDialog; modal: true; anchors.centerIn: parent; width: 460; standardButtons: Dialog.NoButton
        background: ModalBackground {}
        footer: ModalActions { dialog: duplicateDialog; dismissText: "关闭" }
        contentItem: ColumnLayout {
            spacing: 10
            ModalTag { section: "SUBMISSION"; label: "已有成功成果" }
            ModalTitle { text: "已有成功转录的音频" }
            Label { Layout.fillWidth: true; wrapMode: Text.WrapAnywhere; text: root.duplicateRecords.length + " 项音频已有成功登记。普通提交不会重跑，这些条目仍留在「选择音频」。"; color: "#3d3d3d"; font.pixelSize: 12 }
            Repeater {
                model: root.duplicateRecords
                delegate: WorkButton {
                    required property var modelData
                    Layout.fillWidth: true
                    text: "核查结果  " + modelData.inputPath.split(/[\\/]/).pop()
                    onClicked: { duplicateDialog.close(); facade.command("inspectResult", {successRecordId: modelData.successRecordId}) }
                }
            }
        }
    }
    Dialog {
        id: resultDialog; modal: true; anchors.centerIn: parent; width: 480; standardButtons: Dialog.NoButton
        background: ModalBackground {}
        footer: ModalActions { dialog: resultDialog; dismissText: "关闭" }
        contentItem: ColumnLayout {
            spacing: 10
            ModalTag { section: "RESULT"; label: "成果核查" }
            ModalTitle { text: "成功结果核查" }
            Label { Layout.fillWidth: true; wrapMode: Text.Wrap; text: root.inspectedResult.outputStatus === "match" ? "登记的 MIDI 可用" : "登记的 MIDI 缺失或不匹配"; color: "#3d3d3d"; font.pixelSize: 12 }
            ValBox { Layout.fillWidth: true; visible: !!root.inspectedResult.outputPath; text: root.inspectedResult.outputPath || "" }
            WorkButton { text: "打开目录"; visible: root.inspectedResult.outputStatus === "match"; onClicked: facade.openContainingFolder(root.inspectedResult.outputPath) }
            WorkButton {
                text: "恢复成果"
                visible: root.inspectedResult.outputStatus !== "match"
                primary: true
                onClicked: facade.command("beginRecovery", {successRecordId: root.inspectedResult.successRecordId, revision: root.inspectedResult.revision})
            }
            WorkButton {
                text: "重新定位原音频"
                visible: root.inspectedResult.outputStatus !== "match"
                onClicked: facade.pickSuccessInput(root.inspectedResult.successRecordId, root.inspectedResult.revision)
            }
        }
    }
    Dialog {
        id: closeDialog; modal: true; anchors.centerIn: parent; width: 460; standardButtons: Dialog.NoButton
        background: ModalBackground {}
        contentItem: ColumnLayout {
            spacing: 10
            ModalTag { section: "APPLICATION"; label: "关闭确认" }
            ModalTitle { text: "还有 " + root.data.active.length + " 项任务尚未结束" }
            Label { Layout.fillWidth: true; wrapMode: Text.Wrap; text: "继续到托盘可保留正在运行的任务。停止并退出会取消这些任务，并立即终止引擎子进程，不等待 GPU 前向返回。"; color: "#3d3d3d"; font.pixelSize: 12 }
            RowLayout {
                Layout.fillWidth: true
                spacing: 8
                WorkButton { text: "返回"; onClicked: closeDialog.close() }
                WorkButton { text: "停止全部并退出"; onClicked: Qt.quit() }
                Item { Layout.fillWidth: true }
                WorkButton { text: "继续到托盘"; primary: true; onClicked: { closeDialog.close(); root.hide() } }
            }
        }
    }
}
