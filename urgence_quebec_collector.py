#!/usr/bin/env python3
"""
Collecteur de données des urgences du Québec (MSSS)
Récupère les données horaires et les envoie à Splunk via HEC
"""

import csv
import json
import logging
import time
from datetime import datetime
from typing import Dict, List, Optional
import requests
import yaml
from pathlib import Path

# Configuration du logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class UrgencesQuebecCollector:
    """Collecteur de données des urgences du Québec"""
    
    def __init__(self, config_path: str = "config.yaml"):
        """
        Initialise le collecteur avec la configuration
        
        Args:
            config_path: Chemin vers le fichier de configuration YAML
        """
        self.config = self._load_config(config_path)
        self.csv_url = self.config['data_source']['url']
        self.hec_url = self.config['splunk']['hec_url']
        self.hec_token = self.config['splunk']['hec_token']
        self.source = self.config['splunk'].get('source', 'urgences_quebec')
        self.sourcetype = self.config['splunk'].get('sourcetype', 'msss:urgences:csv')
        self.index = self.config['splunk'].get('index', 'main')
        self.verify_ssl = self.config['splunk'].get('verify_ssl', True)
        self.timeout = self.config.get('timeout', 30)
        
        # Options de debug
        debug_config = self.config.get('debug', {})
        self.print_json_output = debug_config.get('print_json_output', False)
        self.max_events_to_print = debug_config.get('max_events_to_print', 3)
        
        # Log de la config debug
        logger.info(f"Option debug - print_json_output: {self.print_json_output}")
        if self.print_json_output:
            logger.info(f"Option debug - max_events_to_print: {self.max_events_to_print}")
            logger.info("DEBUG MODE ACTIVÉ - Le JSON sera affiché dans la console")
        
    def _load_config(self, config_path: str) -> Dict:
        """
        Charge la configuration depuis le fichier YAML
        
        Args:
            config_path: Chemin vers le fichier de configuration
            
        Returns:
            Dictionnaire de configuration
        """
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                config = yaml.safe_load(f)
            logger.info(f"Configuration chargée depuis {config_path}")
            return config
        except FileNotFoundError:
            logger.error(f"Fichier de configuration non trouvé: {config_path}")
            raise
        except yaml.YAMLError as e:
            logger.error(f"Erreur de parsing YAML: {e}")
            raise
    
    def _remove_accents(self, text: str) -> str:
        """
        Enlève les accents des caractères en utilisant un mapping manuel
        
        Args:
            text: Texte à nettoyer
            
        Returns:
            Texte sans accents
        """
        if not isinstance(text, str):
            return text
        
        # Mapping des caractères accentués vers non-accentués
        accent_map = {
            'à': 'a', 'á': 'a', 'â': 'a', 'ã': 'a', 'ä': 'a', 'å': 'a',
            'À': 'A', 'Á': 'A', 'Â': 'A', 'Ã': 'A', 'Ä': 'A', 'Å': 'A',
            'è': 'e', 'é': 'e', 'ê': 'e', 'ë': 'e',
            'È': 'E', 'É': 'E', 'Ê': 'E', 'Ë': 'E',
            'ì': 'i', 'í': 'i', 'î': 'i', 'ï': 'i',
            'Ì': 'I', 'Í': 'I', 'Î': 'I', 'Ï': 'I',
            'ò': 'o', 'ó': 'o', 'ô': 'o', 'õ': 'o', 'ö': 'o',
            'Ò': 'O', 'Ó': 'O', 'Ô': 'O', 'Õ': 'O', 'Ö': 'O',
            'ù': 'u', 'ú': 'u', 'û': 'u', 'ü': 'u',
            'Ù': 'U', 'Ú': 'U', 'Û': 'U', 'Ü': 'U',
            'ý': 'y', 'ÿ': 'y',
            'Ý': 'Y', 'Ÿ': 'Y',
            'ç': 'c', 'Ç': 'C',
            'ñ': 'n', 'Ñ': 'N',
            'æ': 'ae', 'Æ': 'AE',
            'œ': 'oe', 'Œ': 'OE',
            ''': "'", ''': "'", '"': '"', '"': '"',
            '–': '-', '—': '-',
        }
        
        result = []
        for char in text:
            result.append(accent_map.get(char, char))
        
        return ''.join(result)
    
    def fetch_csv_data(self) -> List[Dict]:
        """
        Récupère les données CSV depuis le site du MSSS
        
        Returns:
            Liste de dictionnaires contenant les données
        """
        try:
            logger.info(f"Récupération des données depuis {self.csv_url}")
            response = requests.get(self.csv_url, timeout=self.timeout)
            response.raise_for_status()
            
            # Forcer latin-1 qui peut lire n'importe quels bytes sans erreur
            # Les accents seront ensuite nettoyés par _remove_accents
            response.encoding = 'latin-1'
            
            # Parse le CSV
            csv_content = response.text.splitlines()
            reader = csv.DictReader(csv_content)
            data = list(reader)
            
            logger.info(f"{len(data)} enregistrements récupérés (encodage: latin-1)")
            return data
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Erreur lors de la récupération des données: {e}")
            raise
        except csv.Error as e:
            logger.error(f"Erreur lors du parsing CSV: {e}")
            raise
    
    def transform_data(self, raw_data: List[Dict]) -> List[Dict]:
        """
        Transforme les données brutes pour Splunk
        
        Args:
            raw_data: Données brutes du CSV
            
        Returns:
            Liste de données transformées
        """
        transformed = []
        current_time = int(time.time())
        
        for row in raw_data:
            # Nettoie les clés et valeurs, enlève les accents
            cleaned_row = {}
            for k, v in row.items():
                # Nettoie la clé et enlève les accents
                clean_key = self._remove_accents(k.strip())
                
                # Nettoie la valeur et enlève les accents
                if isinstance(v, str):
                    original_value = v.strip()
                    clean_value = self._remove_accents(original_value)
                    
                    # Log de debug pour le premier élément
                    if len(transformed) == 0 and original_value != clean_value:
                        logger.info(f"Transformation accent - '{original_value}' → '{clean_value}'")
                else:
                    clean_value = v
                
                cleaned_row[clean_key] = clean_value
            
            # Ajoute des métadonnées
            cleaned_row['data_collection_time'] = datetime.now().isoformat()
            cleaned_row['data_source'] = 'MSSS Quebec'
            
            transformed.append(cleaned_row)
        
        logger.info(f"{len(transformed)} enregistrements transformés (accents enlevés)")
        if transformed:
            # Log un exemple
            sample = transformed[0]
            if 'Nom_installation' in sample:
                logger.info(f"Exemple - Nom_installation: {sample['Nom_installation']}")
            elif 'Nom_etablissement' in sample:
                logger.info(f"Exemple - Nom_etablissement: {sample['Nom_etablissement']}")
        
        return transformed
    
    def send_to_splunk(self, data: List[Dict]) -> bool:
        """
        Envoie les données à Splunk via HEC
        
        Args:
            data: Liste de données à envoyer
            
        Returns:
            True si l'envoi est réussi, False sinon
        """
        if not data:
            logger.warning("Aucune donnée à envoyer")
            return False
        
        headers = {
            'Authorization': f'Splunk {self.hec_token}',
            'Content-Type': 'application/json'
        }
        
        # Prépare les événements pour HEC
        events = []
        current_time = time.time()
        
        for record in data:
            event = {
                'time': current_time,
                'source': self.source,
                'sourcetype': self.sourcetype,
                'index': self.index,
                'event': record
            }
            events.append(event)
        
        # Afficher le JSON si l'option debug est activée
        if self.print_json_output:
            print("\n" + "=" * 80)
            print("DEBUG: JSON ENVOYÉ À SPLUNK HEC")
            print("=" * 80)
            events_to_show = min(len(events), self.max_events_to_print)
            for i, event in enumerate(events[:events_to_show]):
                print(f"\nÉvénement {i+1}/{len(events)}:")
                # Forcer l'affichage ASCII pour voir vraiment ce qui est envoyé
                json_str = json.dumps(event, indent=2, ensure_ascii=True)
                print(json_str)
                
                # Vérifier s'il y a des accents dans les données
                event_str = json.dumps(event, ensure_ascii=False)
                accent_chars = ['é', 'è', 'ê', 'ë', 'à', 'â', 'ô', 'ö', 'û', 'ù', 'î', 'ï', 'ç',
                               'É', 'È', 'Ê', 'Ë', 'À', 'Â', 'Ô', 'Ö', 'Û', 'Ù', 'Î', 'Ï', 'Ç']
                found_accents = [c for c in accent_chars if c in event_str]
                if found_accents:
                    print(f"\n⚠️  ATTENTION: Accents trouvés dans cet événement: {found_accents}")
                    print("   → La fonction _remove_accents n'a pas été appliquée correctement!")
                else:
                    print("\n✅ Aucun accent détecté dans cet événement")
            
            if len(events) > self.max_events_to_print:
                print(f"\n... et {len(events) - self.max_events_to_print} autres événements")
            print("=" * 80 + "\n")
        
        # Envoie par batch si nécessaire
        batch_size = self.config['splunk'].get('batch_size', 100)
        total_sent = 0
        
        try:
            for i in range(0, len(events), batch_size):
                batch = events[i:i + batch_size]
                
                # Format pour HEC (un événement par ligne)
                payload = '\n'.join([json.dumps(event) for event in batch])
                
                response = requests.post(
                    self.hec_url,
                    headers=headers,
                    data=payload,
                    verify=self.verify_ssl,
                    timeout=self.timeout
                )
                
                response.raise_for_status()
                result = response.json()
                
                if result.get('code') == 0:
                    total_sent += len(batch)
                    logger.info(f"Batch envoyé avec succès: {len(batch)} événements")
                else:
                    logger.error(f"Erreur HEC: {result}")
                    return False
            
            logger.info(f"Total envoyé: {total_sent} événements")
            return True
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Erreur lors de l'envoi à Splunk: {e}")
            return False
        except json.JSONDecodeError as e:
            logger.error(f"Erreur lors de l'encodage JSON: {e}")
            return False
    
    def run(self) -> bool:
        """
        Execute le processus complet de collecte et envoi
        
        Returns:
            True si le processus est réussi, False sinon
        """
        try:
            logger.info("=== Début de la collecte des données des urgences ===")
            
            # Récupère les données
            raw_data = self.fetch_csv_data()
            
            if not raw_data:
                logger.warning("Aucune donnée récupérée")
                return False
            
            # Transforme les données
            transformed_data = self.transform_data(raw_data)
            
            # Envoie à Splunk
            success = self.send_to_splunk(transformed_data)
            
            if success:
                logger.info("=== Collecte terminée avec succès ===")
            else:
                logger.error("=== Collecte terminée avec erreurs ===")
            
            return success
            
        except Exception as e:
            logger.error(f"Erreur inattendue: {e}", exc_info=True)
            return False


def main():
    """Point d'entrée principal du script"""
    import argparse
    
    parser = argparse.ArgumentParser(
        description='Collecteur de données des urgences du Québec vers Splunk'
    )
    parser.add_argument(
        '--config',
        default='config.yaml',
        help='Chemin vers le fichier de configuration (défaut: config.yaml)'
    )
    
    args = parser.parse_args()
    
    try:
        collector = UrgencesQuebecCollector(config_path=args.config)
        success = collector.run()
        exit(0 if success else 1)
    except Exception as e:
        logger.error(f"Erreur fatale: {e}", exc_info=True)
        exit(1)


if __name__ == '__main__':
    main()
