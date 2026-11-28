(function () {
  "use strict";

  var taskId = new URLSearchParams(window.location.search).get("task") || "";
  var directions = [
    { d: 8, reverse: 2, bit: 1, dx: 0, dy: -1 },
    { d: 6, reverse: 4, bit: 2, dx: 1, dy: 0 },
    { d: 2, reverse: 8, bit: 4, dx: 0, dy: 1 },
    { d: 4, reverse: 6, bit: 8, dx: -1, dy: 0 }
  ];
  var tileMemory = Object.create(null);
  var memoryMapId = 0;

  function emptyState() {
    return {
      ready: false,
      map_visible: false,
      scene: null,
      save_screen: { active: false, selected_index: null },
      map_id: 0,
      player: null,
      visible_tiles: [],
      observed_tiles: [],
      events: [],
      message: {
        busy: false,
        visible: false,
        text: "",
        choices: [],
        choice_index: null
      }
    };
  }

  function sceneName(currentScene) {
    return currentScene && currentScene.constructor
      ? currentScene.constructor.name
      : "loading";
  }

  function visibleEventIds(mapId) {
    // These are the task's authored interaction points; never enumerate other events.
    if (taskId === "431_01" && mapId === 8) return [7, 11];
    if (taskId === "431_03" && mapId === 6) return [12];
    if (taskId === "431_04" && mapId === 25) return [17];
    return [];
  }

  function screenPointInViewport(x, y, width, height) {
    return Number.isFinite(x) && Number.isFinite(y) &&
      x >= 0 && x < width && y >= 0 && y < height;
  }

  function tileIntersectsViewport(x, y, displayX, displayY, tileWidth, tileHeight,
                                  width, height) {
    var left = (x - displayX) * tileWidth;
    var top = (y - displayY) * tileHeight;
    return left + tileWidth > 0 && left < width &&
      top + tileHeight > 0 && top < height;
  }

  function visibleMessageText(messageWindow) {
    if (!messageWindow || !messageWindow.visible ||
        Number(messageWindow.openness) <= 0) return "";
    var state = messageWindow._textState;
    if (!state || typeof state.text !== "string") return "";
    var end = Number(state.index);
    if (!Number.isFinite(end)) return "";
    var text = state.text.slice(0, Math.max(0, Math.min(state.text.length, end)));
    return text
      .replace(/\x1b(?:[A-Z]+\[[^\]]*\]|.)/gi, "")
      .replace(/[\r\n]+/g, " ")
      .trim();
  }

  function currentMessage(currentScene) {
    var gameMessage = window.$gameMessage;
    var messageWindow = currentScene && currentScene._messageWindow;
    var choiceWindow = messageWindow && messageWindow._choiceWindow;
    var visible = !!(messageWindow && messageWindow.visible &&
      Number(messageWindow.openness) > 0);
    var choicesVisible = !!(visible && choiceWindow && choiceWindow.active === true);
    var result = {
      busy: !!(gameMessage && typeof gameMessage.isBusy === "function" && gameMessage.isBusy()),
      visible: visible,
      text: visibleMessageText(messageWindow),
      choices: [],
      choice_index: null
    };
    if (!gameMessage) return result;
    if (choicesVisible && typeof gameMessage.choices === "function") {
      result.choices = gameMessage.choices().map(function (choice) {
        return String(choice);
      });
    }
    if (choicesVisible && typeof choiceWindow.index === "function") {
      var index = Number(choiceWindow.index());
      if (Number.isFinite(index)) result.choice_index = index;
    }
    return result;
  }

  function messageWindowCovers(messageWindow, x, y) {
    if (!messageWindow || !messageWindow.visible || Number(messageWindow.openness) <= 0) {
      return false;
    }
    var left = Number(messageWindow.x) || 0;
    var top = Number(messageWindow.y) || 0;
    var right = left + (Number(messageWindow.width) || 0);
    var bottom = top + (Number(messageWindow.height) || 0);
    return x >= left && x < right && y >= top && y < bottom;
  }

  function getState() {
    var map = window.$gameMap;
    var player = window.$gamePlayer;
    var currentScene = window.SceneManager && SceneManager._scene;
    if (!map || !player || !currentScene || typeof map.isPassable !== "function" ||
        typeof map.width !== "function" || typeof map.height !== "function") {
      return emptyState();
    }

    var name = sceneName(currentScene);
    if (name === "Scene_Title") {
      tileMemory = Object.create(null);
      memoryMapId = 0;
    }
    var currentMapId = Number(map.mapId()) || 0;
    if (memoryMapId !== currentMapId) {
      tileMemory = Object.create(null);
      memoryMapId = currentMapId;
    }

    var saveWindow = name === "Scene_Save" ? currentScene._listWindow : null;
    var saveIndex = null;
    if (saveWindow && saveWindow.active && typeof saveWindow.index === "function") {
      var rawSaveIndex = Number(saveWindow.index());
      if (Number.isFinite(rawSaveIndex)) saveIndex = rawSaveIndex;
    }

    var gameScreen = window.$gameScreen;
    var brightness = gameScreen && typeof gameScreen.brightness === "function"
      ? Number(gameScreen.brightness())
      : 255;
    var fadeDuration = Number(currentScene._fadeDuration);
    var fadeSprite = currentScene._fadeSprite;
    var fadeSpriteVisible = !!(fadeSprite && Number(fadeSprite.opacity) > 0);
    var mapVisible = name === "Scene_Map" &&
      (!Number.isFinite(fadeDuration) || fadeDuration <= 0) &&
      (!Number.isFinite(brightness) || brightness >= 255) && !fadeSpriteVisible;

    var message = currentMessage(currentScene);
    var visibleTiles = [];
    var displayX = typeof map.displayX === "function" ? Number(map.displayX()) || 0 : 0;
    var displayY = typeof map.displayY === "function" ? Number(map.displayY()) || 0 : 0;
    var tileWidth = typeof map.tileWidth === "function" ? Number(map.tileWidth()) || 48 : 48;
    var tileHeight = typeof map.tileHeight === "function" ? Number(map.tileHeight()) || 48 : 48;
    var screenWidth = window.Graphics && Number(Graphics.width) || window.innerWidth || 816;
    var screenHeight = window.Graphics && Number(Graphics.height) || window.innerHeight || 624;

    function isVisibleTile(x, y) {
      return tileIntersectsViewport(x, y, displayX, displayY,
        tileWidth, tileHeight, screenWidth, screenHeight);
    }

    if (mapVisible) {
      var minX = Math.max(0, Math.floor(displayX));
      var maxX = Math.min(Number(map.width()) - 1,
        Math.ceil(displayX + screenWidth / tileWidth) - 1);
      var minY = Math.max(0, Math.floor(displayY));
      var maxY = Math.min(Number(map.height()) - 1,
        Math.ceil(displayY + screenHeight / tileHeight) - 1);

      for (var y = minY; y <= maxY; y += 1) {
        for (var x = minX; x <= maxX; x += 1) {
          if (!isVisibleTile(x, y)) continue;
          var mask = 0;
          directions.forEach(function (direction) {
            var nx = x + direction.dx;
            var ny = y + direction.dy;
            if (!isVisibleTile(nx, ny)) return;
            if (map.isPassable(x, y, direction.d) &&
                map.isPassable(nx, ny, direction.reverse)) {
              mask |= direction.bit;
            }
          });
          visibleTiles.push({ x: x, y: y, mask: mask });
          var key = String(x) + "," + String(y);
          var previous = tileMemory[key];
          tileMemory[key] = previous ? (previous | mask) : mask;
        }
      }
    }

    var messageWindow = currentScene._messageWindow;
    var playerState = null;
    if (mapVisible && !player.isTransparent()) {
      var playerScreenX = typeof player.screenX === "function" ? Number(player.screenX()) : NaN;
      var playerScreenY = typeof player.screenY === "function" ? Number(player.screenY()) : NaN;
      if (screenPointInViewport(playerScreenX, playerScreenY, screenWidth, screenHeight) &&
          !messageWindowCovers(messageWindow, playerScreenX, playerScreenY)) {
        playerState = {
          x: Math.round(Number(player.x) || 0),
          y: Math.round(Number(player.y) || 0),
          direction: Number(player.direction()) || 2,
          moving: !!player.isMoving()
        };
      }
    }

    var events = [];
    if (mapVisible && typeof map.event === "function") {
      visibleEventIds(currentMapId).forEach(function (id) {
        var event = map.event(id);
        if (!event || event._erased ||
            (typeof event.isTransparent === "function" && event.isTransparent()) ||
            (typeof event.page === "function" && !event.page()) ||
            (typeof event.screenX !== "function" || typeof event.screenY !== "function")) {
          return;
        }
        var sx = Number(event.screenX());
        var sy = Number(event.screenY());
        if (!screenPointInViewport(sx, sy, screenWidth, screenHeight) ||
            messageWindowCovers(messageWindow, sx, sy)) return;
        events.push({
          id: id,
          x: Math.round(Number(event.x) || 0),
          y: Math.round(Number(event.y) || 0),
          trigger: Number.isFinite(Number(event._trigger)) ? Number(event._trigger) : 0,
          solid: !(typeof event.isThrough === "function" && event.isThrough()) &&
            !(typeof event.isNormalPriority === "function" && !event.isNormalPriority()),
          moving: typeof event.isMoving === "function" && !!event.isMoving()
        });
      });
    }

    var observedTiles = Object.keys(tileMemory).map(function (key) {
      var parts = key.split(",");
      return { x: Number(parts[0]), y: Number(parts[1]), mask: tileMemory[key] };
    });

    return {
      ready: name === "Scene_Map" && !player.isTransferring(),
      map_visible: mapVisible,
      scene: name,
      save_screen: {
        active: !!(saveWindow && saveWindow.active),
        selected_index: saveIndex
      },
      map_id: mapVisible ? currentMapId : 0,
      player: playerState,
      visible_tiles: visibleTiles,
      observed_tiles: observedTiles,
      events: events,
      message: message
    };
  }

  window.__policyProbe = { getState: getState };
}());
