// Shown once a baseline profile exists. Summarizes the saved baseline and
// exposes the raw UserBaselineProfile JSON — the exact object the
// anomaly-detection and AI-call modules will consume.

import React, { useState } from 'react';
import { View, Text, ScrollView, Alert, StyleSheet } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { Card, Button, InfoBanner } from '../components/ui';
import {
  DAYS, LIVING_SITUATION_OPTIONS, MOBILITY_OPTIONS, SHARE_OPTIONS, labelFor,
} from '../model/profile';
import { formatTime, ageFromDob } from '../utils/datetime';
import { colors, type } from '../theme';

function Row({ label, value }) {
  return (
    <View style={styles.row}>
      <Text style={styles.rowLabel}>{label}</Text>
      <Text style={styles.rowValue}>{value || '—'}</Text>
    </View>
  );
}

export default function ProfileHomeScreen({ profile, onEdit, onReset }) {
  const [showJson, setShowJson] = useState(false);
  const d = profile.demographics;
  const primary = profile.emergencyContacts.find((c) => c.isPrimary);
  const routineCount = DAYS.reduce((n, day) => n + (profile.weeklyRoutine[day]?.length || 0), 0);

  const confirmReset = () => {
    Alert.alert('Start over?', 'This deletes the saved baseline profile.', [
      { text: 'Cancel', style: 'cancel' },
      { text: 'Delete & restart', style: 'destructive', onPress: onReset },
    ]);
  };

  return (
    <SafeAreaView style={styles.safe} edges={['top', 'bottom']}>
      <ScrollView contentContainerStyle={styles.scrollContent}>
        <Text style={type.title}>Care Companion</Text>

        <InfoBanner tone="success">
          ✓ Baseline profile saved for {d.name}. The wearable now has a picture of what a
          normal day looks like.
        </InfoBanner>

        <Card>
          <Text style={[type.label, { marginBottom: 4 }]}>Wearable</Text>
          <Text style={type.small}>
            Not paired yet — pairing arrives in the next build. The profile already
            reserves a deviceId for it.
          </Text>
        </Card>

        <Card>
          <Text style={[type.label, { marginBottom: 10 }]}>Baseline at a glance</Text>
          <Row label="Age" value={String(ageFromDob(d.dob))} />
          <Row label="Living" value={labelFor(LIVING_SITUATION_OPTIONS, d.livingSituation)} />
          <Row
            label="Sleep"
            value={`${formatTime(profile.sleep.typicalSleep)} – ${formatTime(profile.sleep.typicalWake)}`}
          />
          <Row label="Routine" value={`${routineCount} regular activities across the week`} />
          <Row label="Mobility" value={labelFor(MOBILITY_OPTIONS, profile.mobilityLevel)} />
          <Row label="Calls first" value={primary ? `${primary.name} (${primary.phone})` : '—'} />
          <Row
            label="Visible to"
            value={profile.consent.sharedWith.map((v) => labelFor(SHARE_OPTIONS, v)).join(', ')}
          />
        </Card>

        <Button
          title={showJson ? 'Hide raw profile JSON' : 'View raw profile JSON'}
          variant="secondary"
          onPress={() => setShowJson((v) => !v)}
          style={{ marginBottom: 14 }}
        />
        {showJson && (
          <Card>
            <Text style={type.small}>
              UserBaselineProfile — the contract read by the anomaly-detection and
              AI-call modules:
            </Text>
            <Text selectable style={styles.json}>
              {JSON.stringify(profile, null, 2)}
            </Text>
          </Card>
        )}

        <Button title="Edit profile" onPress={onEdit} style={{ marginBottom: 10 }} />
        <Button title="Start over" variant="ghost" onPress={confirmReset} />
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.bg },
  scrollContent: { padding: 20, gap: 0 },
  row: { flexDirection: 'row', marginBottom: 6 },
  rowLabel: { ...type.small, fontSize: 15, width: 100 },
  rowValue: { ...type.body, fontSize: 15, flex: 1 },
  json: {
    fontFamily: 'Courier',
    fontSize: 12,
    color: colors.text,
    marginTop: 10,
    lineHeight: 17,
  },
});
