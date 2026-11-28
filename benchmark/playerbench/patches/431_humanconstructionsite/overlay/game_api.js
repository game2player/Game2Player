(function () {
    "use strict";

    var GAME_ID = "431_humanconstructionsite";
    var taskId = new URLSearchParams(window.location.search).get("task") || "";
    var validTasks = { "431_01": true, "431_02": true, "431_03": true, "431_04": true };
    var startedAt = Date.now();
    var nativeStartAttempted = false;
    var resetInProgress = false;
    var renderedMapScene = null;
    var taskStartReached = false;
    var taskStartSnapshot = null;
    var storageExitReached = false;
    var storageMatchesAtExit = 0;
    var storageBothMatchedDuringRun = false;
    var pendingStorageExit = null;

    function scene() {
        return window.SceneManager && SceneManager._scene ? SceneManager._scene : null;
    }

    function sceneName() {
        var current = scene();
        return current && current.constructor ? current.constructor.name : "loading";
    }

    function mapId() {
        return window.$gameMap && typeof $gameMap.mapId === "function" ? Number($gameMap.mapId()) || 0 : 0;
    }

    function mapName() {
        var info = window.$dataMapInfos && $dataMapInfos[mapId()];
        return info && info.name ? String(info.name) : "";
    }

    function switchValue(id) {
        return !!(window.$gameSwitches && typeof $gameSwitches.value === "function" && $gameSwitches.value(id));
    }

    function variableValue(id) {
        if (!window.$gameVariables || typeof $gameVariables.value !== "function") return 0;
        var value = Number($gameVariables.value(id));
        return Number.isFinite(value) ? value : 0;
    }

    function event(id) {
        return window.$gameMap && typeof $gameMap.event === "function" ? $gameMap.event(id) : null;
    }

    function positionOf(id) {
        var obj = event(id);
        return obj ? { x: Number(obj.x) || 0, y: Number(obj.y) || 0 } : null;
    }

    function storageMatches() {
        if (mapId() !== 6) return 0;
        var table = positionOf(1);
        var shelf = positionOf(4);
        var tableExit = positionOf(7);
        var shelfExit = positionOf(8);
        return Number(!!table && !!tableExit && table.x === tableExit.x && table.y === tableExit.y) +
            Number(!!shelf && !!shelfExit && shelf.x === shelfExit.x && shelf.y === shelfExit.y);
    }

    function adjacentToEvent(eventId) {
        if (!window.$gamePlayer) return false;
        var target = event(eventId);
        return !!target && Math.abs(Number($gamePlayer.x) - Number(target.x)) +
            Math.abs(Number($gamePlayer.y) - Number(target.y)) === 1;
    }

    function matchesTaskStart() {
        if (!validTasks[taskId] || !window.$gamePlayer) return false;
        if (taskId === "431_01") {
            return mapId() === 8 && $gamePlayer.x === 12 && $gamePlayer.y === 33 &&
                !switchValue(38) && !switchValue(20);
        }
        if (taskId === "431_02") {
            return mapId() === 6 && $gamePlayer.x === 1 && $gamePlayer.y === 6 &&
                !$gamePlayer.isTransparent() && positionOf(1) && positionOf(1).x === 5 && positionOf(1).y === 6 &&
                positionOf(4) && positionOf(4).x === 10 && positionOf(4).y === 11 &&
                !switchValue(63) && !switchValue(64);
        }
        if (taskId === "431_03") {
            return mapId() === 6 && adjacentToEvent(12) && switchValue(65) && variableValue(3) === 0;
        }
        return mapId() === 25 && adjacentToEvent(17) && !switchValue(17);
    }

    function recordTaskStart() {
        if (taskStartReached || !matchesTaskStart()) return;
        taskStartReached = true;
        resetInProgress = false;
        taskStartSnapshot = {
            map_id: mapId(),
            map_name: mapName(),
            player: { x: Number($gamePlayer.x) || 0, y: Number($gamePlayer.y) || 0 },
            conditions: {
                first_room_switch_38: switchValue(38),
                first_room_switch_20: switchValue(20),
                storage_switch_63: switchValue(63),
                storage_switch_64: switchValue(64),
                noh_switch_65: switchValue(65),
                variable_3: variableValue(3),
                lab_switch_17: switchValue(17)
            }
        };
    }

    function mapReady() {
        return sceneName() === "Scene_Map" && mapId() > 0 && window.$dataMap &&
            window.DataManager && DataManager.isMapLoaded && DataManager.isMapLoaded() &&
            window.$gamePlayer && !$gamePlayer.isTransferring();
    }

    function mapFrameReady() {
        var currentScene = scene();
        return !!currentScene && renderedMapScene === currentScene &&
            window.Graphics && !!Graphics._renderer;
    }

    function isActionable() {
        if (resetInProgress) return false;
        if (validTasks[taskId]) {
            if (!mapReady() || !taskStartReached || !mapFrameReady()) return false;
            var currentScene = scene();
            var fadedIn = !currentScene || Number(currentScene._fadeDuration) <= 0;
            var dialogueOpen = window.$gameMessage && $gameMessage.isBusy && $gameMessage.isBusy();
            if (taskId === "431_01") {
                return !!dialogueOpen || (fadedIn && !$gameMap.isEventRunning() && !$gamePlayer.isTransparent());
            }
            return fadedIn || !!dialogueOpen;
        }
        return mapReady() || (sceneName() === "Scene_Title" && !!scene()._commandWindow);
    }

    function configureTaskStart() {
        if (!validTasks[taskId] || !window.$gamePlayer) return;
        if (taskId === "431_02") {
            $gamePlayer.reserveTransfer(6, 1, 6, 6, 0);
            if (typeof $gamePlayer.setTransparent === "function") $gamePlayer.setTransparent(false);
        } else if (taskId === "431_03") {
            $gameSwitches.setValue(65, true);
            $gameVariables.setValue(3, 0);
            $gamePlayer.reserveTransfer(6, 4, 10, 8, 0);
            if (typeof $gamePlayer.setTransparent === "function") $gamePlayer.setTransparent(false);
        } else if (taskId === "431_04") {
            $gameSwitches.setValue(17, false);
            $gamePlayer.reserveTransfer(25, 33, 8, 8, 0);
            if (typeof $gamePlayer.setTransparent === "function") $gamePlayer.setTransparent(false);
        }
    }

    function installHooks() {
        if (window.Graphics && typeof Graphics.render === "function" && !Graphics._humanConstructionApiRenderHook) {
            var originalRender = Graphics.render;
            Graphics.render = function (stage) {
                var result = originalRender.apply(this, arguments);
                var currentScene = scene();
                if (this._rendered && stage && stage === currentScene &&
                    currentScene.constructor && currentScene.constructor.name === "Scene_Map" && mapReady()) {
                    renderedMapScene = currentScene;
                }
                return result;
            };
            Graphics._humanConstructionApiRenderHook = true;
        }

        if (window.Scene_Title && !Scene_Title.prototype._humanConstructionApiStartHook) {
            var originalTitleStart = Scene_Title.prototype.start;
            Scene_Title.prototype.start = function () {
                originalTitleStart.apply(this, arguments);
                if (validTasks[taskId] && !nativeStartAttempted) {
                    nativeStartAttempted = true;
                    this.commandNewGame();
                }
            };
            var originalNewGame = Scene_Title.prototype.commandNewGame;
            Scene_Title.prototype.commandNewGame = function () {
                var result = originalNewGame.apply(this, arguments);
                configureTaskStart();
                return result;
            };
            Scene_Title.prototype._humanConstructionApiStartHook = true;
        }

        if (window.Game_Interpreter && !Game_Interpreter.prototype._humanConstructionApiStorageExitHook) {
            var originalTransferCommand = Game_Interpreter.prototype.command201;
            Game_Interpreter.prototype.command201 = function () {
                var params = this._params || [];
                if (taskId === "431_02" && mapId() === 6 && this._eventId === 13 && Number(params[1]) === 31) {
                    var currentMatches = storageMatches();
                    if (currentMatches === 2) storageBothMatchedDuringRun = true;
                    pendingStorageExit = { matches: currentMatches, priorBothMatched: storageBothMatchedDuringRun };
                }
                return originalTransferCommand.apply(this, arguments);
            };
            Game_Interpreter.prototype._humanConstructionApiStorageExitHook = true;
        }

        if (window.Game_Player && !Game_Player.prototype._humanConstructionApiTransferHook) {
            var originalPerformTransfer = Game_Player.prototype.performTransfer;
            Game_Player.prototype.performTransfer = function () {
                var fromMap = mapId();
                var toMap = this.isTransferring() ? Number(this.newMapId()) : 0;
                var exit = pendingStorageExit;
                if (toMap > 0) renderedMapScene = null;
                var result = originalPerformTransfer.apply(this, arguments);
                if (nativeStartAttempted && !taskStartReached && toMap > 0) recordTaskStart();
                if (exit && fromMap === 6 && toMap === 31 && exit.priorBothMatched && mapId() === 31) {
                    storageExitReached = true;
                    storageMatchesAtExit = 2;
                }
                if (exit && fromMap === 6 && toMap === 31) pendingStorageExit = null;
                return result;
            };
            Game_Player.prototype._humanConstructionApiTransferHook = true;
        }
    }

    installHooks();

    function taskSuccess() {
        if (taskId === "431_01") return switchValue(20);
        if (taskId === "431_02") return storageExitReached && mapId() === 31;
        if (taskId === "431_03") return variableValue(3) > 0;
        if (taskId === "431_04") return switchValue(17);
        return false;
    }

    function getState() {
        installHooks();
        var now = Date.now();
        var positions = {
            table: positionOf(1),
            shelf: positionOf(4),
            table_exit: positionOf(7),
            shelf_exit: positionOf(8),
            noh: positionOf(12),
            lab_lever: positionOf(17)
        };
        var liveStorageMatches = storageMatches();
        if (liveStorageMatches === 2) storageBothMatchedDuringRun = true;
        var storageSteps = storageExitReached ? storageMatchesAtExit + 1 : liveStorageMatches;
        var success = taskSuccess();
        var failed = sceneName() === "Scene_Gameover";
        var player = window.$gamePlayer ? {
            x: Number($gamePlayer.x) || 0,
            y: Number($gamePlayer.y) || 0,
            direction: Number($gamePlayer.direction()) || 0,
            visible: !$gamePlayer.isTransparent(),
            moving: !!$gamePlayer.isMoving()
        } : null;
        var state = {
            scene: sceneName(),
            requested_task: taskId || null,
            map_id: mapId(),
            map_name: mapName(),
            player: player,
            task_start_verified: taskStartReached,
            start_snapshot: taskStartSnapshot,
            task_success: success,
            switches: {
                first_room_hand_slot: switchValue(38),
                first_room_gate_unlocked: switchValue(20),
                storage_table_matched: !!(positions.table && positions.table_exit && positions.table.x === positions.table_exit.x && positions.table.y === positions.table_exit.y),
                storage_shelf_matched: !!(positions.shelf && positions.shelf_exit && positions.shelf.x === positions.shelf_exit.x && positions.shelf.y === positions.shelf_exit.y),
                storage_exit_event13: storageExitReached,
                storage_both_matched_during_run: storageBothMatchedDuringRun,
                noh_trust_scene_enabled: switchValue(65),
                lab_lever_pulled: switchValue(17)
            },
            variables: { variable_3: variableValue(3) },
            positions: positions,
            provenance: {
                map_id: "internal",
                player_position: "internal",
                switches: "internal",
                variables: "internal",
                storage_exit_event: "engine_hook"
            }
        };
        var status = success ? "success" : failed ? "fail" : mapReady() ? "playing" : sceneName() === "Scene_Title" ? "title" : "loading";
        return {
            schemaVersion: "2.0",
            gameId: GAME_ID,
            timestampMs: now,
            gameTimeMs: Math.max(0, now - startedAt),
            status: status,
            is_actionable: isActionable(),
            terminal: {
                isTerminal: !!success || !!failed,
                outcome: success ? "success" : failed ? "fail" : null,
                reason: success ? "task_objective_reached" : failed ? "game_over" : null
            },
            game_state: state,
            metrics: {
                first_room_escape_steps: Number(switchValue(38)) + Number(switchValue(20)),
                storage_escape_steps: storageSteps,
                nou_trust_choice: Number(variableValue(3) > 0),
                lab_lever_pulled: Number(switchValue(17))
            }
        };
    }

    window.gameAPI = {
        version: "2.0",
        capabilities: {
            supports_seed: true,
            supports_level_select: true,
            supports_difficulty: false,
            supports_inplace_reset: true,
            supports_reload_reset: false,
            supports_pause_detection: false,
            supports_menu_detection: true,
            provides_actionable_flag: true
        },
        init: async function () {
            installHooks();
            return { ok: true, task: taskId || null, start_method: "Scene_Title.commandNewGame" };
        },
        getState: getState,
        reset: async function () {
            if (validTasks[taskId] && window.SceneManager && window.Scene_Title) {
                storageExitReached = false;
                storageMatchesAtExit = 0;
                storageBothMatchedDuringRun = false;
                pendingStorageExit = null;
                taskStartReached = false;
                taskStartSnapshot = null;
                renderedMapScene = null;
                resetInProgress = true;
                nativeStartAttempted = false;
                if (typeof window.__resetRandomSeed === "function") window.__resetRandomSeed();
                SceneManager.goto(Scene_Title);
                return { ok: true, method: "Scene_Title.commandNewGame" };
            }
            window.location.reload();
            return { ok: true, method: "reload" };
        }
    };
}());
