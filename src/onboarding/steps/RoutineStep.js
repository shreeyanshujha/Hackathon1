import React, { useState } from 'react';
import { View, Text, Alert, StyleSheet } from 'react-native';
import { Card, Field, TextField, TimeField, ChoiceChips, Button, InfoBanner } from '../../components/ui';
import { DAYS, DAY_LABELS, DAY_LABELS_SHORT, ACTIVITY_SUGGESTIONS } from '../../model/profile';
import { parseTime, formatTime } from '../../utils/datetime';
import { colors, type } from '../../theme';

export default function RoutineStep({ draft, setDraft }) {
  const [day, setDay] = useState('monday');
  const [form, setForm] = useState({ activity: '', time: '', duration: '' });
  const [formError, setFormError] = useState(null);

  const routine = draft.weeklyRoutine;
  const entries = routine[day];

  const dayOptions = DAYS.map((d) => ({
    value: d,
    label: routine[d].length ? `${DAY_LABELS_SHORT[d]} · ${routine[d].length}` : DAY_LABELS_SHORT[d],
  }));

  const setDayEntries = (targetDay, list) =>
    setDraft({ weeklyRoutine: { ...routine, [targetDay]: list } });

  const addEntry = () => {
    const activity = form.activity.trim();
    const expectedTime = parseTime(form.time);
    const expectedDuration = parseInt(form.duration, 10);
    if (!activity) return setFormError('Give the activity a name.');
    if (!expectedTime) return setFormError('Enter an expected time, e.g. 8:30 am.');
    if (!expectedDuration || expectedDuration <= 0) return setFormError('Enter an expected duration in minutes.');

    const next = [...entries, { activity, expectedTime, expectedDuration }].sort((a, b) =>
      a.expectedTime.localeCompare(b.expectedTime),
    );
    setDayEntries(day, next);
    setForm({ activity: '', time: '', duration: '' });
    setFormError(null);
  };

  const removeEntry = (index) => setDayEntries(day, entries.filter((_, i) => i !== index));

  const copyToAllDays = () => {
    Alert.alert(
      'Copy to every day?',
      `This replaces the other days with ${DAY_LABELS[day]}'s routine.`,
      [
        { text: 'Cancel', style: 'cancel' },
        {
          text: 'Copy',
          onPress: () => {
            const next = {};
            for (const d of DAYS) next[d] = entries.map((e) => ({ ...e }));
            setDraft({ weeklyRoutine: next });
          },
        },
      ],
    );
  };

  return (
    <View>
      <InfoBanner>
        This is what lets the system tell “still because it’s 3 am” apart from “still at
        11 am on a day they’re normally out walking”. Add the regular things — walks,
        shopping, visitors, clubs. Quiet days can stay empty.
      </InfoBanner>

      <Field label="Day">
        <ChoiceChips options={dayOptions} value={day} onChange={setDay} />
      </Field>

      {entries.length === 0 ? (
        <Text style={styles.emptyText}>Nothing added for {DAY_LABELS[day]} yet.</Text>
      ) : (
        entries.map((entry, index) => (
          <Card key={`${entry.activity}-${entry.expectedTime}-${index}`} style={styles.entryCard}>
            <View style={{ flex: 1 }}>
              <Text style={type.label}>{entry.activity}</Text>
              <Text style={type.small}>
                {formatTime(entry.expectedTime)} · about {entry.expectedDuration} min
              </Text>
            </View>
            <Text style={styles.removeLink} onPress={() => removeEntry(index)}>
              ✕
            </Text>
          </Card>
        ))
      )}

      <Card style={{ marginTop: 6 }}>
        <Text style={[type.label, { marginBottom: 10 }]}>Add an activity to {DAY_LABELS[day]}</Text>
        <View style={styles.suggestionRow}>
          {ACTIVITY_SUGGESTIONS.map((s) => (
            <Text key={s} style={styles.suggestion} onPress={() => setForm({ ...form, activity: s })}>
              {s}
            </Text>
          ))}
        </View>
        <Field label="Activity" style={styles.formField}>
          <TextField
            value={form.activity}
            onChangeText={(activity) => setForm({ ...form, activity })}
            placeholder="e.g. Morning walk"
          />
        </Field>
        <View style={styles.formRow}>
          <Field label="Expected time" style={[styles.formField, { flex: 1 }]}>
            <TimeField
              value={form.time}
              onChangeText={(time) => setForm({ ...form, time })}
              placeholder="8:30 am"
            />
          </Field>
          <Field label="Duration (min)" style={[styles.formField, { width: 120 }]}>
            <TextField
              value={form.duration}
              onChangeText={(duration) => setForm({ ...form, duration })}
              placeholder="30"
              keyboardType="number-pad"
            />
          </Field>
        </View>
        {formError ? <Text style={styles.formError}>{formError}</Text> : null}
        <Button title="Add activity" variant="secondary" onPress={addEntry} />
      </Card>

      {entries.length > 0 && (
        <Button title={`Copy ${DAY_LABELS[day]}'s routine to every day`} variant="ghost" onPress={copyToAllDays} />
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  emptyText: { ...type.small, fontSize: 15, marginBottom: 12, fontStyle: 'italic' },
  entryCard: { flexDirection: 'row', alignItems: 'center', paddingVertical: 12 },
  removeLink: { color: colors.danger, fontSize: 18, fontWeight: '700', padding: 8 },
  suggestionRow: { flexDirection: 'row', flexWrap: 'wrap', gap: 8, marginBottom: 12 },
  suggestion: {
    color: colors.primaryDark,
    backgroundColor: colors.primarySoft,
    borderRadius: 16,
    paddingHorizontal: 12,
    paddingVertical: 7,
    fontSize: 14,
    overflow: 'hidden',
  },
  formField: { marginBottom: 12 },
  formRow: { flexDirection: 'row', gap: 12 },
  formError: { color: colors.danger, fontSize: 13, fontWeight: '600', marginBottom: 10 },
});
