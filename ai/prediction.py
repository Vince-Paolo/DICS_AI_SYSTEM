import csv
import os
from pathlib import Path

from joblib import dump, load
from sklearn.ensemble import RandomForestRegressor
from sklearn.linear_model import LinearRegression
from sklearn.model_selection import cross_val_score
from sklearn.svm import SVR

TRAINING_CSV = os.path.join(os.path.abspath(os.path.dirname(__file__)), '..', 'data', 'hazard_training.csv')
MODEL_FILE = os.path.join(os.path.abspath(os.path.dirname(__file__)), '..', 'instance', 'hazard_predictor.joblib')

class HazardPredictor:
    def __init__(self):
        self.models = {}
        self.metrics = {}
        self.default_hazard = 'flood'
        self._ensure_model_path()
        if self._training_data_is_newer_than_model() or not self._load_model():
            self._train_models()
            self._save_model()

    def _ensure_model_path(self):
        model_dir = os.path.dirname(MODEL_FILE)
        os.makedirs(model_dir, exist_ok=True)

    def _load_training_data(self):
        training_data = {}
        csv_path = Path(TRAINING_CSV)
        if not csv_path.exists():
            raise FileNotFoundError(f'Training file not found: {csv_path}')

        with csv_path.open(newline='') as csvfile:
            reader = csv.DictReader(csvfile)
            for row in reader:
                hazard_type = row.get('hazard_type', '').strip().lower()
                if not hazard_type:
                    continue
                features = [
                    float(row.get('rainfall_mm', 0) or 0),
                    float(row.get('river_level_m', 0) or 0),
                    float(row.get('soil_moisture_pct', 0) or 0),
                    float(row.get('population_density', 0) or 0),
                ]
                score = float(row.get('score', 0) or 0)
                training_data.setdefault(hazard_type, []).append((features, score))

        return training_data

    def _train_models(self):
        training_data = self._load_training_data()
        if not training_data:
            raise ValueError('No training data available for hazard prediction')

        self.default_hazard = next(iter(training_data.keys()))
        for hazard_type, examples in training_data.items():
            X = [features for features, score in examples]
            y = [score for features, score in examples]
            linear_model = LinearRegression()
            rf_model = RandomForestRegressor(n_estimators=100, random_state=42)
            svm_model = SVR(kernel='rbf', C=100.0, gamma=0.1)
            models = {
                'linear_regression': linear_model,
                'random_forest': rf_model,
                'svm': svm_model,
            }
            cv_folds = min(5, len(y))
            if cv_folds > 1:
                scores = cross_val_score(linear_model, X, y, cv=cv_folds, scoring='neg_mean_squared_error')
                rmse = float(((-scores).mean()) ** 0.5)
                self.metrics[hazard_type] = {
                    'cv_folds': cv_folds,
                    'cv_rmse': round(rmse, 2),
                    'training_examples': len(y),
                    'models': ['linear_regression', 'random_forest', 'svm'],
                }
            else:
                self.metrics[hazard_type] = {
                    'cv_folds': 0,
                    'cv_rmse': None,
                    'training_examples': len(y),
                    'models': ['linear_regression', 'random_forest', 'svm'],
                }
            for name, model in models.items():
                model.fit(X, y)
            self.models[hazard_type] = {
                'linear_regression': linear_model,
                'random_forest': rf_model,
                'svm': svm_model,
            }

    def _save_model(self):
        payload = {
            'models': self.models,
            'metrics': self.metrics,
            'default_hazard': self.default_hazard,
        }
        dump(payload, MODEL_FILE)

    def _training_data_is_newer_than_model(self):
        if not os.path.exists(MODEL_FILE):
            return True
        try:
            training_mtime = os.path.getmtime(TRAINING_CSV)
            model_mtime = os.path.getmtime(MODEL_FILE)
            return training_mtime > model_mtime
        except OSError:
            return True

    def _load_model(self):
        if not os.path.exists(MODEL_FILE):
            return False
        try:
            payload = load(MODEL_FILE)
            self.models = payload.get('models', {})
            self.metrics = payload.get('metrics', {})
            self.default_hazard = payload.get('default_hazard', self.default_hazard)
            return bool(self.models)
        except Exception:
            return False

    def predict(self, hazard_type, rainfall_mm, river_level_m, soil_moisture_pct, population_density):
        hazard_type = hazard_type.strip().lower() if isinstance(hazard_type, str) else self.default_hazard
        model_group = self.models.get(hazard_type) or self.models.get(self.default_hazard)
        if not model_group:
            raise RuntimeError('No hazard prediction model is available')

        X = [[rainfall_mm, river_level_m, soil_moisture_pct, population_density]]
        linear_score = model_group['linear_regression'].predict(X)[0]
        rf_score = model_group['random_forest'].predict(X)[0]
        svm_score = model_group['svm'].predict(X)[0]
        score = float((linear_score + rf_score + svm_score) / 3.0)
        score = max(0.0, min(100.0, round(score, 1)))

        if score < 25:
            level = 'Low'
            message = 'Low hazard risk. Continue monitoring conditions.'
        elif score < 50:
            level = 'Moderate'
            message = 'Moderate hazard risk. Prepare to respond as conditions change.'
        elif score < 75:
            level = 'High'
            message = 'High hazard risk. Take action and follow precautionary procedures.'
        else:
            level = 'Severe'
            message = 'Severe hazard risk. Activate emergency response and evacuate if needed.'

        return {
            'type': hazard_type,
            'score': score,
            'level': level,
            'message': message,
            'alert': score >= 50,
        }

predictor = HazardPredictor()


def predict_hazard(hazard_type, rainfall_mm, river_level_m, soil_moisture_pct, population_density):
    return predictor.predict(hazard_type, rainfall_mm, river_level_m, soil_moisture_pct, population_density)
