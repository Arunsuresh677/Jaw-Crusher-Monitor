// Mirrors the JSON shape from crusher_logic.get_state() and crusher_camera.get_state()

class AlertModel {
  final String id;
  final String level;
  final String message;
  final String timestamp;
  final bool resolved;

  const AlertModel({
    required this.id,
    required this.level,
    required this.message,
    required this.timestamp,
    required this.resolved,
  });

  factory AlertModel.fromJson(Map<String, dynamic> j) => AlertModel(
        id: j['id'] as String? ?? '',
        level: j['level'] as String? ?? 'info',
        message: j['message'] as String? ?? '',
        timestamp: j['timestamp'] as String? ?? '',
        resolved: j['resolved'] as bool? ?? false,
      );
}

class ShiftInfo {
  final String shift;
  final String start;
  final double elapsedMinutes;

  const ShiftInfo({required this.shift, required this.start, required this.elapsedMinutes});

  factory ShiftInfo.fromJson(Map<String, dynamic> j) => ShiftInfo(
        shift: j['shift'] as String? ?? 'day',
        start: j['start'] as String? ?? '--:--',
        elapsedMinutes: (j['elapsed_minutes'] as num?)?.toDouble() ?? 0,
      );

  static const empty = ShiftInfo(shift: 'day', start: '--:--', elapsedMinutes: 0);
}

class CrusherState {
  // Jaw detection
  final String jawLabel;
  final double jawConf;

  // Machine
  final String machineStatus;
  final String statusSince;

  // VFD
  final int targetVfdHz;

  // Timers (HH:MM:SS strings)
  final String timerRun;
  final String timerStuck;
  final String timerNoFeed;
  final String timerIdle;
  final String timerShift;

  // OEE
  final double availabilityPct;
  final int framesRunning;
  final int framesStuck;
  final int framesNoFeed;
  final int frameCount;
  final double tonnageActual;

  // Alerts
  final List<AlertModel> activeAlerts;
  final int alertCount;

  // Shift
  final ShiftInfo shift;

  // Camera
  final String camStatus;
  final double cameraFps;
  final String? frameBase64; // JPEG frame from WebSocket

  const CrusherState({
    this.jawLabel = 'unknown',
    this.jawConf = 0,
    this.machineStatus = 'STOPPED',
    this.statusSince = '--:--:--',
    this.targetVfdHz = 0,
    this.timerRun = '00:00:00',
    this.timerStuck = '00:00:00',
    this.timerNoFeed = '00:00:00',
    this.timerIdle = '00:00:00',
    this.timerShift = '00:00:00',
    this.availabilityPct = 0,
    this.framesRunning = 0,
    this.framesStuck = 0,
    this.framesNoFeed = 0,
    this.frameCount = 0,
    this.tonnageActual = 0,
    this.activeAlerts = const [],
    this.alertCount = 0,
    this.shift = ShiftInfo.empty,
    this.camStatus = 'stopped',
    this.cameraFps = 0,
    this.frameBase64,
  });

  static const empty = CrusherState();

  factory CrusherState.fromJson(Map<String, dynamic> j) {
    final alerts = (j['active_alerts'] as List<dynamic>? ?? [])
        .map((a) => AlertModel.fromJson(a as Map<String, dynamic>))
        .toList();
    return CrusherState(
      jawLabel: j['jaw_label'] as String? ?? 'unknown',
      jawConf: (j['jaw_conf'] as num?)?.toDouble() ?? 0,
      machineStatus: j['machine_status'] as String? ?? 'STOPPED',
      statusSince: j['status_since'] as String? ?? '--:--:--',
      targetVfdHz: (j['target_vfd_hz'] as num?)?.toInt() ?? 0,
      timerRun: j['timer_run'] as String? ?? '00:00:00',
      timerStuck: j['timer_stuck'] as String? ?? '00:00:00',
      timerNoFeed: j['timer_no_feed'] as String? ?? '00:00:00',
      timerIdle: j['timer_idle'] as String? ?? '00:00:00',
      timerShift: j['timer_shift'] as String? ?? '00:00:00',
      availabilityPct: (j['availability_pct'] as num?)?.toDouble() ?? 0,
      framesRunning: (j['frames_running'] as num?)?.toInt() ?? 0,
      framesStuck: (j['frames_stuck'] as num?)?.toInt() ?? 0,
      framesNoFeed: (j['frames_no_feed'] as num?)?.toInt() ?? 0,
      frameCount: (j['frame_count'] as num?)?.toInt() ?? 0,
      tonnageActual: (j['tonnage_actual'] as num?)?.toDouble() ?? 0,
      activeAlerts: alerts,
      alertCount: (j['alert_count'] as num?)?.toInt() ?? alerts.length,
      shift: j['shift'] != null ? ShiftInfo.fromJson(j['shift'] as Map<String, dynamic>) : ShiftInfo.empty,
      camStatus: j['cam_status'] as String? ?? 'stopped',
      cameraFps: (j['camera_fps'] as num?)?.toDouble() ?? 0,
      frameBase64: j['frame'] as String?,
    );
  }

  CrusherState copyWith({String? frameBase64}) => CrusherState(
        jawLabel: jawLabel,
        jawConf: jawConf,
        machineStatus: machineStatus,
        statusSince: statusSince,
        targetVfdHz: targetVfdHz,
        timerRun: timerRun,
        timerStuck: timerStuck,
        timerNoFeed: timerNoFeed,
        timerIdle: timerIdle,
        timerShift: timerShift,
        availabilityPct: availabilityPct,
        framesRunning: framesRunning,
        framesStuck: framesStuck,
        framesNoFeed: framesNoFeed,
        frameCount: frameCount,
        tonnageActual: tonnageActual,
        activeAlerts: activeAlerts,
        alertCount: alertCount,
        shift: shift,
        camStatus: camStatus,
        cameraFps: cameraFps,
        frameBase64: frameBase64 ?? this.frameBase64,
      );

  bool get isNormal => machineStatus == 'NORMAL';
  bool get isStuck => machineStatus == 'STONE STUCK';
  bool get isNoFeed => machineStatus == 'NO RAW MATERIAL';
  bool get isStopped => machineStatus == 'STOPPED' || machineStatus == 'FAULT';
}
