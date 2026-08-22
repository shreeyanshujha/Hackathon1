// Shared form building blocks used by every onboarding step.

import React, { useState } from 'react';
import { View, Text, TextInput, Switch, Pressable, StyleSheet } from 'react-native';
import { colors, type } from '../theme';
import { parseTime, formatTime } from '../utils/datetime';

export function Card({ children, style }) {
  return <View style={[styles.card, style]}>{children}</View>;
}

export function Field({ label, hint, error, children, style }) {
  return (
    <View style={[styles.field, style]}>
      {label ? <Text style={styles.fieldLabel}>{label}</Text> : null}
      {children}
      {error ? <Text style={styles.fieldError}>{error}</Text> : hint ? <Text style={styles.fieldHint}>{hint}</Text> : null}
    </View>
  );
}

export function TextField({ error, style, ...props }) {
  return (
    <TextInput
      placeholderTextColor="#9AACB6"
      style={[styles.input, props.multiline && styles.inputMultiline, error && styles.inputError, style]}
      {...props}
    />
  );
}

// Free-typed time entry ('7:30 pm', '19:00', '7') with a live confirmation of
// how it was understood.
export function TimeField({ value, onChangeText, error, placeholder }) {
  const parsed = parseTime(value);
  return (
    <View>
      <TextField
        value={value}
        onChangeText={onChangeText}
        placeholder={placeholder}
        autoCapitalize="none"
        autoCorrect={false}
        error={error}
      />
      {value && parsed ? <Text style={styles.timeEcho}>Recorded as {formatTime(parsed)}</Text> : null}
    </View>
  );
}

export function ChoiceChips({ options, value, onChange, multi = false }) {
  const isSelected = (v) => (multi ? (value || []).includes(v) : value === v);
  const toggle = (v) => {
    if (!multi) return onChange(v);
    const current = value || [];
    onChange(current.includes(v) ? current.filter((x) => x !== v) : [...current, v]);
  };
  return (
    <View style={styles.chipRow}>
      {options.map((opt) => {
        const selected = isSelected(opt.value);
        return (
          <Pressable
            key={opt.value}
            onPress={() => toggle(opt.value)}
            style={[styles.chip, selected && styles.chipSelected]}
          >
            <Text style={[styles.chipText, selected && styles.chipTextSelected]}>{opt.label}</Text>
          </Pressable>
        );
      })}
    </View>
  );
}

export function SwitchRow({ label, sublabel, value, onValueChange }) {
  return (
    <View style={styles.switchRow}>
      <View style={{ flex: 1, paddingRight: 12 }}>
        <Text style={type.label}>{label}</Text>
        {sublabel ? <Text style={styles.fieldHint}>{sublabel}</Text> : null}
      </View>
      <Switch
        value={value}
        onValueChange={onValueChange}
        trackColor={{ false: colors.border, true: colors.primary }}
        thumbColor="#FFFFFF"
      />
    </View>
  );
}

// Numeric stepper that supports a "not set" (null) state below zero.
export function StepperInput({ value, onChange, max = 30 }) {
  const decrement = () => onChange(value === null || value === 0 ? null : value - 1);
  const increment = () => onChange(value === null ? 0 : Math.min(value + 1, max));
  return (
    <View style={styles.stepperRow}>
      <Pressable onPress={decrement} style={styles.stepperButton}>
        <Text style={styles.stepperButtonText}>−</Text>
      </Pressable>
      <Text style={styles.stepperValue}>{value === null ? 'Not set' : value}</Text>
      <Pressable onPress={increment} style={styles.stepperButton}>
        <Text style={styles.stepperButtonText}>+</Text>
      </Pressable>
    </View>
  );
}

export function Button({ title, onPress, variant = 'primary', disabled, style }) {
  return (
    <Pressable
      onPress={onPress}
      disabled={disabled}
      style={({ pressed }) => [
        styles.button,
        styles[`button_${variant}`],
        pressed && { opacity: 0.75 },
        disabled && { opacity: 0.4 },
        style,
      ]}
    >
      <Text style={[styles.buttonText, styles[`buttonText_${variant}`]]}>{title}</Text>
    </Pressable>
  );
}

export function InfoBanner({ children, tone = 'info' }) {
  const tones = {
    info: { backgroundColor: colors.primarySoft, color: colors.primaryDark },
    success: { backgroundColor: colors.successSoft, color: colors.success },
    danger: { backgroundColor: colors.dangerSoft, color: colors.danger },
    warning: { backgroundColor: colors.warningSoft, color: colors.warning },
  };
  const t = tones[tone] || tones.info;
  return (
    <View style={[styles.banner, { backgroundColor: t.backgroundColor }]}>
      <Text style={[styles.bannerText, { color: t.color }]}>{children}</Text>
    </View>
  );
}

// Selected tags + tappable suggestions + free-text add. Used for hobbies and
// self-reported health conditions.
export function TagEditor({ tags, onChange, suggestions = [], placeholder }) {
  const [text, setText] = useState('');
  const add = (raw) => {
    const value = raw.trim();
    if (!value) return;
    if (!tags.some((t) => t.toLowerCase() === value.toLowerCase())) onChange([...tags, value]);
    setText('');
  };
  const remove = (tag) => onChange(tags.filter((t) => t !== tag));
  const remaining = suggestions.filter((s) => !tags.some((t) => t.toLowerCase() === s.toLowerCase()));

  return (
    <View>
      {tags.length > 0 && (
        <View style={styles.chipRow}>
          {tags.map((tag) => (
            <Pressable key={tag} onPress={() => remove(tag)} style={[styles.chip, styles.chipSelected]}>
              <Text style={styles.chipTextSelected}>{tag}  ✕</Text>
            </Pressable>
          ))}
        </View>
      )}
      {remaining.length > 0 && (
        <View style={styles.chipRow}>
          {remaining.map((s) => (
            <Pressable key={s} onPress={() => add(s)} style={styles.chip}>
              <Text style={styles.chipText}>+ {s}</Text>
            </Pressable>
          ))}
        </View>
      )}
      <View style={styles.tagInputRow}>
        <TextField
          value={text}
          onChangeText={setText}
          placeholder={placeholder}
          onSubmitEditing={() => add(text)}
          returnKeyType="done"
          style={{ flex: 1 }}
        />
        <Button title="Add" variant="secondary" onPress={() => add(text)} style={styles.tagAddButton} />
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    backgroundColor: colors.card,
    borderRadius: 14,
    borderWidth: 1,
    borderColor: colors.border,
    padding: 16,
    marginBottom: 14,
  },
  field: { marginBottom: 18 },
  fieldLabel: { ...type.label, marginBottom: 8 },
  fieldHint: { ...type.small, marginTop: 6 },
  fieldError: { fontSize: 13, color: colors.danger, marginTop: 6, fontWeight: '600' },
  input: {
    backgroundColor: colors.card,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: 12,
    paddingHorizontal: 14,
    paddingVertical: 12,
    fontSize: 17,
    color: colors.text,
  },
  inputMultiline: { minHeight: 80, textAlignVertical: 'top' },
  inputError: { borderColor: colors.danger },
  timeEcho: { fontSize: 13, color: colors.success, marginTop: 6, fontWeight: '600' },
  chipRow: { flexDirection: 'row', flexWrap: 'wrap', gap: 8, marginBottom: 8 },
  chip: {
    borderWidth: 1,
    borderColor: colors.border,
    backgroundColor: colors.card,
    borderRadius: 20,
    paddingHorizontal: 14,
    paddingVertical: 9,
  },
  chipSelected: { backgroundColor: colors.primary, borderColor: colors.primary },
  chipText: { fontSize: 15, color: colors.text },
  chipTextSelected: { fontSize: 15, color: '#FFFFFF', fontWeight: '600' },
  switchRow: { flexDirection: 'row', alignItems: 'center', paddingVertical: 4 },
  stepperRow: { flexDirection: 'row', alignItems: 'center', gap: 16 },
  stepperButton: {
    width: 48,
    height: 48,
    borderRadius: 12,
    backgroundColor: colors.primarySoft,
    alignItems: 'center',
    justifyContent: 'center',
  },
  stepperButtonText: { fontSize: 24, color: colors.primaryDark, fontWeight: '700' },
  stepperValue: { fontSize: 18, fontWeight: '600', color: colors.text, minWidth: 70, textAlign: 'center' },
  button: {
    borderRadius: 12,
    paddingVertical: 15,
    paddingHorizontal: 18,
    alignItems: 'center',
    justifyContent: 'center',
  },
  button_primary: { backgroundColor: colors.primary },
  button_secondary: { backgroundColor: colors.primarySoft },
  button_danger: { backgroundColor: colors.dangerSoft },
  button_ghost: { backgroundColor: 'transparent' },
  buttonText: { fontSize: 17, fontWeight: '700' },
  buttonText_primary: { color: '#FFFFFF' },
  buttonText_secondary: { color: colors.primaryDark },
  buttonText_danger: { color: colors.danger },
  buttonText_ghost: { color: colors.subtext },
  banner: { borderRadius: 12, padding: 14, marginBottom: 16 },
  bannerText: { fontSize: 15, lineHeight: 21 },
  tagInputRow: { flexDirection: 'row', gap: 8, alignItems: 'center' },
  tagAddButton: { paddingVertical: 12 },
});
