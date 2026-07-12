ALTER TABLE users ADD COLUMN telegram_id BIGINT UNIQUE;
ALTER TABLE users ADD COLUMN telegram_username VARCHAR(50);
ALTER TABLE users ADD COLUMN chat_id BIGINT; -- Identique à telegram_id, pratique pour l'envoi de messages
<?php
// Configuration BDD
define('DB_HOST', 'localhost');
define('DB_NAME', 'votre_base');
define('DB_USER', 'votre_user');
define('DB_PASS', 'votre_pass');

// Token du Bot Telegram (à obtenir via @BotFather)
define('BOT_TOKEN', 'VOTRE_TOKEN_ICI');

// URL de votre site (obligatoirement en HTTPS pour les webhooks)
define('SITE_URL', 'https://votre-domaine.com');

// Taux de conversion (0.35 USDT ≈ 200 FCFA fixe, ou dynamique via API)
define('MEMBERSHIP_FEE_FCFA', 200);
?>
<?php
require_once 'config.php';
require_once 'db.php';
require_once 'User.php';
require_once 'Matrix.php';
require_once 'Commission.php';
require_once 'Payment.php';

// Fonction utilitaire pour envoyer des messages à Telegram
function sendTelegram($chat_id, $text, $keyboard = null) {
    $url = "https://api.telegram.org/bot" . BOT_TOKEN . "/sendMessage";
    $data = [
        'chat_id' => $chat_id,
        'text' => $text,
        'parse_mode' => 'HTML'
    ];
    if ($keyboard) {
        $data['reply_markup'] = json_encode($keyboard);
    }
    $options = [
        'http' => [
            'header' => "Content-type: application/x-www-form-urlencoded\r\n",
            'method' => 'POST',
            'content' => http_build_query($data)
        ]
    ];
    $context = stream_context_create($options);
    file_get_contents($url, false, $context);
}

// Récupération de l'entrée Telegram
$input = file_get_contents('php://input');
$update = json_decode($input, true);

if (!$update) {
    http_response_code(400);
    exit;
}

// Gestion des messages textuels
if (isset($update['message'])) {
    $message = $update['message'];
    $chat_id = $message['chat']['id'];
    $text = $message['text'] ?? '';
    $telegram_id = $message['from']['id'];
    $username = $message['from']['username'] ?? '';

    // Vérifier si l'utilisateur est déjà enregistré en BDD
    $stmt = $pdo->prepare("SELECT id, is_active, balance FROM users WHERE telegram_id = ?");
    $stmt->execute([$telegram_id]);
    $user = $stmt->fetch(PDO::FETCH_ASSOC);

    // Commande /start
    if (strpos($text, '/start') === 0) {
        // Extraire l'ID du parrain (ex: /start 123)
        $parts = explode(' ', $text);
        $sponsor_id = $parts[1] ?? null;

        if ($user) {
            // Si déjà inscrit, rediriger vers le tableau de bord
            sendTelegram($chat_id, "✅ Bienvenue de retour ! Utilisez /dashboard pour voir vos statistiques.");
        } else {
            // Nouvel utilisateur : on l'enregistre en BDD (inactif)
            $userObj = new User($pdo);
            $newId = $userObj->registerTelegram($telegram_id, $username, $sponsor_id);

            if ($newId) {
                // Bouton de paiement
                $keyboard = [
                    'inline_keyboard' => [
                        [
                            ['text' => '💳 Payer mon adhésion (200 FCFA)', 'callback_data' => 'pay_'.$newId]
                        ]
                    ]
                ];
                sendTelegram($chat_id, 
                    "🔄 <b>Inscription en attente de paiement</b>\n\n".
                    "Votre adhésion est de <b>".MEMBERSHIP_FEE_FCFA." FCFA</b> (équivalent 0.35 USDT).\n".
                    "Cliquez sur le bouton ci-dessous pour payer via Mobile Money.",
                    $keyboard
                );
            } else {
                sendTelegram($chat_id, "❌ Erreur lors de l'inscription. Vérifiez que vous n'êtes pas déjà enregistré.");
            }
        }
    }
    // Commande /dashboard
    elseif ($text === '/dashboard' && $user) {
        // Récupérer le nombre de filleuls (gauche/droite)
        $stmt = $pdo->prepare("SELECT left_child, right_child, level, balance FROM users WHERE id=?");
        $stmt->execute([$user['id']]);
        $data = $stmt->fetch(PDO::FETCH_ASSOC);
        
        $msg = "📊 <b>Votre Tableau de Bord</b>\n";
        $msg .= "👤 ID: #".$user['id']."\n";
        $msg .= "📈 Niveau: ".$data['level']."\n";
        $msg .= "⬅️ Filleul Gauche: ".($data['left_child'] ?: 'Vide')."\n";
        $msg .= "➡️ Filleul Droit: ".($data['right_child'] ?: 'Vide')."\n";
        $msg .= "💰 Solde: ".$data['balance']." FCFA\n";
        $msg .= "📌 Statut: ".($user['is_active'] ? '✅ Actif' : '⏳ En attente de paiement');
        sendTelegram($chat_id, $msg);
    }
    // Autres commandes (/help, etc.)
    else {
        sendTelegram($chat_id, "🤖 Commandes disponibles :\n/start - S'inscrire\n/dashboard - Voir mon compte\n/parrainage - Obtenir mon lien de parrainage");
    }
}

// Gestion des clics sur les boutons inline (CallbackQuery)
if (isset($update['callback_query'])) {
    $callback = $update['callback_query'];
    $chat_id = $callback['message']['chat']['id'];
    $data = $callback['data'];
    $telegram_id = $callback['from']['id'];

    // Répondre au "chargement" pour éviter que le bouton ne reste bloqué
    // file_get_contents("https://api.telegram.org/bot".BOT_TOKEN."/answerCallbackQuery?callback_query_id=".$callback['id']);

    if (strpos($data, 'pay_') === 0) {
        $userId = str_replace('pay_', '', $data);

        // Vérifier que l'utilisateur correspond bien
        $stmt = $pdo->prepare("SELECT id, is_active FROM users WHERE id=? AND telegram_id=?");
        $stmt->execute([$userId, $telegram_id]);
        $user = $stmt->fetch();
        if (!$user) {
            sendTelegram($chat_id, "❌ Utilisateur introuvable.");
            exit;
        }
        if ($user['is_active'] == 1) {
            sendTelegram($chat_id, "✅ Vous êtes déjà actif !");
            exit;
        }

        // Lancer la demande de paiement (Orange/MTN/Wave)
        $payment = new Payment($pdo);
        // Ici, on demande le numéro de téléphone à l'utilisateur (on pourrait le stocker ou le lui demander via un autre message)
        // Pour simplifier, on va lui demander manuellement d'envoyer son numéro
        sendTelegram($chat_id, "📱 Veuillez envoyer votre numéro de téléphone (ex: 77123456) pour recevoir la demande de paiement.");
        
        // On stocke un "état" en attente pour que le prochain message textuel soit interprété comme le numéro
        // (Pour un vrai bot, il faut gérer une session, je simplifie ici)
        // -> Je vais plutôt générer un lien de paiement directement via une API si le numéro est déjà connu, 
        // ou utiliser un autre mécanisme.

        // DANS UNE VRAIE IMPLÉMENTATION, utilisez un système de "step" ou une base de données d'états.
        // Exemple rapide : Créer une transaction en attente et retourner un lien de paiement.
        $txId = $payment->requestPayment($userId, MEMBERSHIP_FEE_FCFA, '77999999', 'telegram'); // Numéro fictif
        sendTelegram($chat_id, "✅ Une demande de paiement de ".MEMBERSHIP_FEE_FCFA." FCFA a été envoyée sur votre téléphone. Confirmez le paiement pour activer votre compte.");
    }
}
?>
class User {
    private $db;
    public function __construct($db) { $this->db = $db; }

    public function registerTelegram($telegram_id, $username, $sponsor_id = null) {
        // Vérifier s'il existe déjà
        $stmt = $this->db->prepare("SELECT id FROM users WHERE telegram_id = ?");
        $stmt->execute([$telegram_id]);
        if ($stmt->fetch()) return false;

        // Créer un nom d'utilisateur unique (ou utiliser son @username Telegram)
        $unique_username = $username ?: 'user_' . $telegram_id;

        // Insérer
        $stmt = $this->db->prepare("INSERT INTO users (username, telegram_id, chat_id, telegram_username, sponsor_id, is_active) VALUES (?, ?, ?, ?, ?, 0)");
        $stmt->execute([$unique_username, $telegram_id, $telegram_id, $username, $sponsor_id]);
        $userId = $this->db->lastInsertId();

        // Placement dans la matrice (si un parrain a été fourni)
        if ($sponsor_id) {
            $matrix = new Matrix($this->db);
            $matrix->placeUser($userId, $sponsor_id);
        } else {
            // Si pas de parrain, on le place en racine (ou sous un parrain système)
            // Pour éviter un arbre sans racine, on peut attribuer un sponsor système (ex: id=1)
            $sponsor_system = 1; // Le premier utilisateur créé manuellement
            $matrix = new Matrix($this->db);
            $matrix->placeUser($userId, $sponsor_system);
        }

        return $userId;
    }
}
// Après validation du paiement
// Récupérer l'utilisateur et son parrain
$stmt = $pdo->prepare("SELECT u.telegram_id AS user_tg, s.telegram_id AS sponsor_tg, u.sponsor_id FROM users u LEFT JOIN users s ON s.id = u.sponsor_id WHERE u.id = ?");
$stmt->execute([$userId]);
$data = $stmt->fetch(PDO::FETCH_ASSOC);

// Envoyer un message à l'utilisateur pour lui dire que c'est activé
if ($data['user_tg']) {
    sendTelegram($data['user_tg'], "🎉 <b>Félicitations !</b> Votre compte est désormais <b>ACTIF</b>. Vous pouvez commencer à construire votre réseau et gagner des commissions.");
}

// Envoyer une notification au parrain
if ($data['sponsor_tg']) {
    sendTelegram($data['sponsor_tg'], "🎯 <b>Nouveau membre dans votre réseau !</b>\nUn de vos filleuls (ID: $userId) vient de payer son adhésion et est désormais actif.");
}

// Distribuer les commissions (votre class Commission déjà codée)
$commission = new Commission($pdo);
$commission->distribute($userId, MEMBERSHIP_FEE_FCFA);
elseif ($text === '/parrainage' && $user) {
    $link = SITE_URL . "/?ref=" . $user['id'];
    // Ou directement un lien Telegram personnalisé : https://t.me/votre_bot?start=ID
    $telegram_link = "https://t.me/votre_bot?start=" . $user['id'];
    sendTelegram($chat_id, "🔗 <b>Votre lien de parrainage Telegram :</b>\n$telegram_link\n\nEnvoyez ce lien à vos amis. Lorsqu'ils cliqueront dessus et paieront, vous recevrez des commissions !");
}
https://api.telegram.org/botVOTRE_TOKEN/setWebhook?url=https://votre-domaine.com/bot-webhook.php
/ (racine)
├── config.php
├── db.php
├── bot-webhook.php        # Point d'entrée Telegram
├── payment-callback.php   # Webhook des opérateurs mobile money
├── User.php
├── Matrix.php
├── Commission.php
├── Payment.php
└── .htaccess
